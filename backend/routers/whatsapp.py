from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from urllib.parse import quote
import re
from database import get_db
from routers.auth import require_auth
from models import Client, MessageTemplate, WhatsappLog, WhatsappStatus
from schemas import WhatsappLinkRequest, TemplateCreate, TemplateOut, WhatsappLogOut

router = APIRouter()


# ── Yardımcılar ───────────────────────────────────────────
def format_amount(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def render_template(body: str, variables: dict) -> str:
    for key, value in variables.items():
        body = body.replace(f"[{key}]", str(value))
    return body

def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", phone)
    if cleaned.startswith("0"):
        cleaned = "90" + cleaned[1:]
    elif cleaned.startswith("+"):
        cleaned = cleaned[1:]
    elif not cleaned.startswith("90"):
        cleaned = "90" + cleaned
    return cleaned

def build_wa_link(phone: str, message: str) -> str:
    normalized = normalize_phone(phone)
    encoded    = quote(message)
    return f"https://wa.me/{normalized}?text={encoded}"


# ── Mesaj Taslakları ──────────────────────────────────────
@router.get("/templates", response_model=List[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(MessageTemplate).order_by(MessageTemplate.name).all()

@router.post("/templates", response_model=TemplateOut)
def create_template(data: TemplateCreate, db: Session = Depends(get_db)):
    if data.is_default and data.payment_type:
        (db.query(MessageTemplate)
         .filter(MessageTemplate.payment_type == data.payment_type,
                 MessageTemplate.is_default == True)
         .update({"is_default": False}))
    t = MessageTemplate(**data.model_dump())
    db.add(t); db.commit(); db.refresh(t)
    return t

@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: str, data: TemplateCreate, db: Session = Depends(get_db)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Taslak bulunamadı")
    for k, v in data.model_dump().items():
        setattr(t, k, v)
    db.commit(); db.refresh(t)
    return t

@router.delete("/templates/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    t = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Taslak bulunamadı")
    db.delete(t); db.commit()
    return {"ok": True}


# ── WhatsApp Link Üret + Kaydet ───────────────────────────
@router.post("/link")
def generate_link(req: WhatsappLinkRequest, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == req.client_id).first()
    if not client:
        raise HTTPException(404, "Müşteri bulunamadı")

    if req.custom_message:
        message = req.custom_message
        payment_type_label = "manuel"
    else:
        if not req.template_id:
            raise HTTPException(400, "template_id veya custom_message gerekli")
        template = db.query(MessageTemplate).filter(MessageTemplate.id == req.template_id).first()
        if not template:
            raise HTTPException(404, "Taslak bulunamadı")
        variables = {
            "isim":  client.full_name,
            "tür":   template.payment_type.value if template.payment_type else "",
            "tutar": format_amount(req.amount) if req.amount else "—",
            "tarih": req.due_date or "—",
        }
        message = render_template(template.body, variables)
        payment_type_label = template.payment_type.value if template.payment_type else None

    wa_link = build_wa_link(client.phone, message)

    # Logu kaydet (link açılınca "gönderildi" sayılır)
    log = WhatsappLog(
        client_id=client.id,
        phone=client.phone,
        message_body=message,
        payment_type=payment_type_label,
        status=WhatsappStatus.opened,
    )
    db.add(log); db.commit(); db.refresh(log)

    return {
        "wa_link":    wa_link,
        "message":    message,
        "phone":      client.phone,
        "client_name": client.full_name,
        "log_id":     str(log.id),
    }


# ── Önizleme (kaydetmeden) ────────────────────────────────
@router.post("/preview")
def preview_message(
    template_id: str,
    client_id:   str,
    amount:      Optional[float] = None,
    due_date:    Optional[str]   = None,
    db:          Session         = Depends(get_db),
):
    template = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    client   = db.query(Client).filter(Client.id == client_id).first()
    if not template or not client:
        raise HTTPException(404, "Taslak veya müşteri bulunamadı")
    variables = {
        "isim":  client.full_name,
        "tür":   template.payment_type.value if template.payment_type else "",
        "tutar": format_amount(amount) if amount else "—",
        "tarih": due_date or "—",
    }
    rendered = render_template(template.body, variables)
    wa_link  = build_wa_link(client.phone, rendered)
    return {"preview": rendered, "wa_link": wa_link, "char_count": len(rendered)}


# ── Loglar ────────────────────────────────────────────────
@router.get("/logs", response_model=List[WhatsappLogOut])
def whatsapp_logs(
    client_id: Optional[str] = None,
    limit:     int           = 200,
    db:        Session       = Depends(get_db),
):
    q = db.query(WhatsappLog)
    if client_id:
        q = q.filter(WhatsappLog.client_id == client_id)
    logs = q.order_by(WhatsappLog.sent_at.desc()).limit(limit).all()
    result = []
    for log in logs:
        d = {c.name: getattr(log, c.name) for c in log.__table__.columns}
        d["client_name"] = log.client.full_name if log.client else None
        result.append(WhatsappLogOut(**d))
    return result


# ── WhatsApp API Ayarları ─────────────────────────────────
from models import AppSettings
import httpx

@router.get("/settings")
def get_wa_settings(db: Session = Depends(get_db), _=Depends(require_auth)):
    s = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not s:
        return {"wa_phone_number_id": None, "wa_access_token": None,
                "wa_template_name": "odeme_hatirlatma", "wa_template_lang": "tr"}
    token = s.wa_access_token
    masked = ("*" * (len(token) - 6) + token[-6:]) if token and len(token) > 6 else None
    return {
        "wa_phone_number_id": s.wa_phone_number_id,
        "wa_access_token_masked": masked,
        "wa_template_name": s.wa_template_name or "odeme_hatirlatma",
        "wa_template_lang": s.wa_template_lang or "tr",
        "configured": bool(s.wa_phone_number_id and s.wa_access_token),
    }

@router.post("/settings")
def save_wa_settings(body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    s = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not s:
        raise HTTPException(404, "Ayar kaydı bulunamadı")
    if "wa_phone_number_id" in body:
        s.wa_phone_number_id = body["wa_phone_number_id"] or None
    if "wa_access_token" in body and body["wa_access_token"] and not body["wa_access_token"].startswith("*"):
        s.wa_access_token = body["wa_access_token"]
    if "wa_template_name" in body:
        s.wa_template_name = body["wa_template_name"] or "odeme_hatirlatma"
    if "wa_template_lang" in body:
        s.wa_template_lang = body["wa_template_lang"] or "tr"
    db.commit()
    return {"ok": True}


# ── Toplu WhatsApp Gönderimi ──────────────────────────────
@router.post("/bulk-send")
async def bulk_send(body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    """
    body: {
      payment_ids: [...],           # hangi ödemeler için gönderilecek
      template_name: "...",         # Meta'da onaylı şablon adı (opsiyonel, ayardan gelir)
      template_lang: "tr"
    }
    Şablon değişkenleri: {{1}}=isim, {{2}}=ödeme türü, {{3}}=tutar, {{4}}=vade tarihi
    """
    from models import Payment

    s = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not s or not s.wa_phone_number_id or not s.wa_access_token:
        raise HTTPException(400, "WhatsApp API ayarları eksik. Ayarlar sayfasından Phone Number ID ve Access Token girin.")

    phone_number_id = s.wa_phone_number_id
    access_token    = s.wa_access_token
    template_name   = body.get("template_name") or s.wa_template_name or "odeme_hatirlatma"
    template_lang   = body.get("template_lang") or s.wa_template_lang or "tr"
    payment_ids     = body.get("payment_ids", [])

    if not payment_ids:
        raise HTTPException(400, "Hiç ödeme seçilmedi.")

    payments = db.query(Payment).filter(Payment.id.in_(payment_ids)).all()

    PT_LABELS = {
        "kdv": "KDV", "stopaj": "Stopaj", "muhasebe_ucreti": "Muhasebe Ücreti",
        "gelir_vergisi": "Gelir Vergisi", "gecici_vergi": "Geçici Vergi",
        "sgk": "SGK Primi", "damga_vergisi": "Damga Vergisi",
        "ozel_iletisim_vergisi": "Özel İletişim Vergisi",
        "otv": "ÖTV", "motorlu_tasit_vergisi": "Motorlu Taşıt Vergisi",
    }

    results = []
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15) as client_http:
        for p in payments:
            if not p.client or not p.client.phone:
                results.append({"payment_id": str(p.id), "status": "skip", "reason": "Telefon numarası yok"})
                continue

            phone     = normalize_phone(p.client.phone)
            isim      = p.client.full_name
            tur       = PT_LABELS.get(p.payment_type.value if p.payment_type else "", str(p.payment_type or ""))
            tutar     = format_amount(float(p.amount)) + " ₺" if p.amount else "—"
            tarih     = p.due_date.strftime("%d.%m.%Y") if p.due_date else "—"

            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": template_lang},
                    "components": [{
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": isim},
                            {"type": "text", "text": tur},
                            {"type": "text", "text": tutar},
                            {"type": "text", "text": tarih},
                        ]
                    }]
                }
            }

            try:
                resp = await client_http.post(url, headers=headers, json=payload)
                resp_json = resp.json()
                if resp.status_code == 200:
                    # Logu kaydet
                    log = WhatsappLog(
                        client_id=p.client_id,
                        phone=p.client.phone,
                        message_body=f"[Toplu API] {isim} — {tur} — {tutar} — {tarih}",
                        payment_type=p.payment_type.value if p.payment_type else None,
                        status=WhatsappStatus.opened,
                    )
                    db.add(log)
                    results.append({"payment_id": str(p.id), "client": isim, "status": "ok"})
                else:
                    err_msg = resp_json.get("error", {}).get("message", str(resp_json))
                    results.append({"payment_id": str(p.id), "client": isim, "status": "error", "reason": err_msg})
            except Exception as e:
                results.append({"payment_id": str(p.id), "client": isim, "status": "error", "reason": str(e)})

    db.commit()
    ok_count  = sum(1 for r in results if r["status"] == "ok")
    err_count = sum(1 for r in results if r["status"] == "error")
    skip_count = sum(1 for r in results if r["status"] == "skip")
    return {"results": results, "ok": ok_count, "error": err_count, "skip": skip_count}
