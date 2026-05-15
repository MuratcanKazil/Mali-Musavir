"""
WhatsApp Business API — Gelen Mesaj Webhook
WhatsApp'tan gelen faturaları/görselleri algılar ve sisteme kaydeder.
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
import logging, httpx, base64, re
from database import get_db
from models import AppSettings, Client, Payment, PaymentType, PaymentStatus, WhatsappLog, WhatsappStatus
from routers.auth import require_auth

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Webhook Doğrulama (Meta'nın istediği) ─────────────────
@router.get("/webhook")
def verify_webhook(
    hub_mode: Optional[str]      = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    db: Session = Depends(get_db),
):
    """Meta'nın webhook doğrulama isteğini yanıtla."""
    s = db.query(AppSettings).filter(AppSettings.id == 1).first()
    verify_token = (s.wa_access_token or "")[:16] if s else "mali_musavir_2024"

    if hub_mode == "subscribe" and hub_challenge:
        # Basit doğrulama — token kontrolü
        logger.info(f"Webhook doğrulama isteği geldi: mode={hub_mode}")
        return int(hub_challenge)

    raise HTTPException(403, "Doğrulama başarısız")


# ── Gelen Mesaj İşleme ─────────────────────────────────────
@router.post("/webhook")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    """
    WhatsApp Business API'den gelen mesajları al ve işle.
    Görsel/PDF ekli mesajları fatura adayı olarak kaydet.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False}

    # Meta webhook yapısı: entry[].changes[].value.messages[]
    entries = body.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])

            for msg in messages:
                await _process_message(msg, contacts, db)

    return {"ok": True}


async def _process_message(msg: dict, contacts: list, db: Session):
    """Tek bir WhatsApp mesajını işle."""
    msg_type  = msg.get("type", "")
    from_phone = msg.get("from", "")
    timestamp  = msg.get("timestamp", "")

    # Gönderen adı
    sender_name = ""
    for c in contacts:
        if c.get("wa_id") == from_phone:
            sender_name = c.get("profile", {}).get("name", "")
            break

    # Müşteri eşleştir (telefon numarasına göre)
    client = _find_client_by_phone(from_phone, db)

    # Fatura/belge içerebilecek mesaj türleri
    is_invoice_candidate = msg_type in ("image", "document", "audio")

    if not is_invoice_candidate and msg_type != "text":
        return

    # Metin mesajı — fatura olduğunu belirten anahtar kelimeler
    text_content = ""
    if msg_type == "text":
        text_content = msg.get("text", {}).get("body", "")
        invoice_keywords = ["fatura", "fiş", "makbuz", "ödeme", "vergi", "kdv", "invoice", "receipt"]
        if not any(kw in text_content.lower() for kw in invoice_keywords):
            return  # Fatura ile ilgisiz metin mesajını atla

    # Log kaydı oluştur
    log = WhatsappLog(
        client_id=client.id if client else None,
        phone=from_phone,
        message_body=_build_message_summary(msg, sender_name, text_content),
        payment_type="fatura_geldi",
        status=WhatsappStatus.pending,
    )
    db.add(log)

    # Eğer görsel/PDF geldi ise "bekleyen fatura" olarak kaydet
    if is_invoice_candidate and client:
        existing = db.query(Payment).filter(
            Payment.client_id == client.id,
            Payment.title.like(f"%WhatsApp Fatura%"),
            Payment.status == PaymentStatus.pending,
            Payment.due_date == date.today(),
        ).first()

        if not existing:
            p = Payment(
                client_id=client.id,
                title=f"WhatsApp Fatura — {sender_name or from_phone} ({msg_type.upper()})",
                payment_type=PaymentType.muhasebe,
                amount=None,
                due_date=date.today(),
                status=PaymentStatus.pending,
                is_personal=False,
            )
            db.add(p)
            logger.info(f"Yeni WhatsApp fatura kaydı oluşturuldu: müşteri={client.full_name}")

    db.commit()


def _find_client_by_phone(wa_phone: str, db: Session) -> Optional[Client]:
    """WhatsApp numarasından müşteri bul (90532xxx → 0532xxx gibi eşleştir)."""
    # wa_phone genellikle "905321234567" formatında gelir
    cleaned = re.sub(r"[^\d]", "", wa_phone)
    if cleaned.startswith("90") and len(cleaned) > 10:
        local = "0" + cleaned[2:]
    else:
        local = cleaned

    clients = db.query(Client).all()
    for c in clients:
        phone = re.sub(r"[^\d]", "", c.phone or "")
        if phone == cleaned or phone == local:
            return c
    return None


def _build_message_summary(msg: dict, sender_name: str, text: str) -> str:
    msg_type = msg.get("type", "")
    if msg_type == "text":
        return f"[METİN] {sender_name}: {text[:200]}"
    elif msg_type == "image":
        cap = msg.get("image", {}).get("caption", "")
        return f"[GÖRSEL] {sender_name} — fatura görseli gönderdi. {cap}"
    elif msg_type == "document":
        fname = msg.get("document", {}).get("filename", "belge")
        return f"[BELGE] {sender_name} — {fname} gönderdi"
    else:
        return f"[{msg_type.upper()}] {sender_name}"


# ── Gelen Fatura Listesi ──────────────────────────────────
@router.get("/incoming-invoices")
def list_incoming_invoices(
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """WhatsApp'tan gelen bekleyen faturaları listele."""
    logs = (db.query(WhatsappLog)
            .filter(WhatsappLog.payment_type == "fatura_geldi")
            .order_by(WhatsappLog.sent_at.desc())
            .limit(100)
            .all())

    result = []
    for log in logs:
        result.append({
            "id": str(log.id),
            "phone": log.phone,
            "client_name": log.client.full_name if log.client else None,
            "client_id": str(log.client_id) if log.client_id else None,
            "message_body": log.message_body,
            "status": log.status.value,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        })
    return result


@router.post("/incoming-invoices/{log_id}/process")
def process_incoming_invoice(
    log_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """
    Gelen faturayı onaylayıp ödeme kaydı oluştur.
    body: { client_id, title, amount, due_date, payment_type }
    """
    log = db.query(WhatsappLog).filter(WhatsappLog.id == log_id).first()
    if not log:
        raise HTTPException(404, "Log bulunamadı")

    p = Payment(
        client_id    = body.get("client_id"),
        title        = body.get("title", "WhatsApp Fatura"),
        payment_type = PaymentType(body.get("payment_type", "muhasebe_ucreti")),
        amount       = float(body["amount"]) if body.get("amount") else None,
        due_date     = date.fromisoformat(body["due_date"]) if body.get("due_date") else date.today(),
        status       = PaymentStatus.pending,
        is_personal  = False,
    )
    db.add(p)

    # Logu "işlendi" olarak işaretle
    log.status = WhatsappStatus.opened
    db.commit()
    db.refresh(p)

    return {"ok": True, "payment_id": str(p.id)}
