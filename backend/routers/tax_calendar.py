from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date
import calendar as cal_mod
from database import get_db
from models import TaxEvent, Payment, Client, PaymentType, PaymentStatus
from routers.auth import require_auth

router = APIRouter()


# ── Türkiye Vergi Takvimi (sabit tanımlar) ────────────────
# Her yıl değişebilir, ama tipik vadeler bunlar.
TURKEY_TAX_CALENDAR = [
    # Aylık ödemeler (her ay)
    {"name": "KDV Beyannamesi",          "payment_type": "kdv",              "applies_to": "monthly",   "day": 26, "description": "Her ayın 26'sına kadar bir önceki aya ait KDV beyannamesi"},
    {"name": "Muhtasar Beyanname",       "payment_type": "stopaj",           "applies_to": "monthly",   "day": 26, "description": "Her ayın 26'sına kadar stopaj/muhtasar beyanname"},
    {"name": "SGK Primi",                "payment_type": "sgk",              "applies_to": "monthly",   "day": 23, "description": "Her ayın 23'üne kadar SGK işveren ve işçi primi"},
    {"name": "Damga Vergisi",            "payment_type": "damga_vergisi",    "applies_to": "monthly",   "day": 26, "description": "Her ayın 26'sına kadar damga vergisi beyannamesi"},
    # 3 aylık ödemeler (Ocak, Nisan, Temmuz, Ekim → önceki çeyrek)
    {"name": "Geçici Vergi (1. Dönem)",  "payment_type": "gecici_vergi",     "applies_to": "quarterly", "months": [5],  "day": 17, "description": "Ocak-Mart dönemi geçici vergi — Mayıs ayında"},
    {"name": "Geçici Vergi (2. Dönem)",  "payment_type": "gecici_vergi",     "applies_to": "quarterly", "months": [8],  "day": 17, "description": "Nisan-Haziran dönemi geçici vergi — Ağustos ayında"},
    {"name": "Geçici Vergi (3. Dönem)",  "payment_type": "gecici_vergi",     "applies_to": "quarterly", "months": [11], "day": 17, "description": "Temmuz-Eylül dönemi geçici vergi — Kasım ayında"},
    {"name": "Geçici Vergi (4. Dönem)",  "payment_type": "gecici_vergi",     "applies_to": "quarterly", "months": [2],  "day": 17, "description": "Ekim-Aralık dönemi geçici vergi — Şubat ayında"},
    # Yıllık ödemeler
    {"name": "Gelir Vergisi Beyannamesi","payment_type": "gelir_vergisi",    "applies_to": "annual",    "months": [3],  "day": 31, "description": "Yıllık gelir vergisi beyannamesi — Mart sonu"},
    {"name": "Kurumlar Vergisi",         "payment_type": "gelir_vergisi",    "applies_to": "annual",    "months": [4],  "day": 30, "description": "Kurumlar vergisi beyannamesi — Nisan sonu"},
    {"name": "MTV (1. Taksit)",          "payment_type": "motorlu_tasit_vergisi", "applies_to": "biannual", "months": [1], "day": 31, "description": "Motorlu taşıtlar vergisi 1. taksit — Ocak"},
    {"name": "MTV (2. Taksit)",          "payment_type": "motorlu_tasit_vergisi", "applies_to": "biannual", "months": [7], "day": 31, "description": "Motorlu taşıtlar vergisi 2. taksit — Temmuz"},
]

MONTH_NAMES_TR = {
    1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
    7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"
}


def get_events_for_month(year: int, month: int) -> list:
    """Verilen yıl/ay için hangi vergi olayları geçerli, tarihlerini hesapla."""
    events = []
    last_day = cal_mod.monthrange(year, month)[1]

    for e in TURKEY_TAX_CALENDAR:
        applies = e["applies_to"]
        due_day = min(e["day"], last_day)

        if applies == "monthly":
            events.append({
                "name": e["name"],
                "payment_type": e["payment_type"],
                "due_date": date(year, month, due_day),
                "description": e["description"],
                "applies_to": applies,
            })
        elif applies in ("quarterly", "annual", "biannual"):
            if month in e.get("months", []):
                events.append({
                    "name": e["name"],
                    "payment_type": e["payment_type"],
                    "due_date": date(year, month, due_day),
                    "description": e["description"],
                    "applies_to": applies,
                })

    events.sort(key=lambda x: x["due_date"])
    return events


# ── Endpoints ─────────────────────────────────────────────

@router.get("/events")
def list_events(
    year:  Optional[int] = None,
    month: Optional[int] = None,
    _=Depends(require_auth),
):
    """Verilen ay için Türkiye vergi takvimi olaylarını döndür."""
    today = date.today()
    y = year  or today.year
    m = month or today.month
    events = get_events_for_month(y, m)
    return {
        "year": y,
        "month": m,
        "month_name": MONTH_NAMES_TR.get(m, ""),
        "events": events,
    }


@router.get("/year-overview")
def year_overview(
    year: Optional[int] = None,
    _=Depends(require_auth),
):
    """Tüm yıl için vergi takvimini döndür."""
    y = year or date.today().year
    all_events = []
    for m in range(1, 13):
        evs = get_events_for_month(y, m)
        all_events.extend(evs)
    return {"year": y, "events": all_events}


@router.post("/auto-create")
def auto_create_payments(
    body: dict,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """
    Seçilen ay + vergi türleri için tüm aktif müşterilere otomatik ödeme kaydı oluştur.
    body: { year, month, payment_types: [...], overwrite: false }
    """
    year  = body.get("year",  date.today().year)
    month = body.get("month", date.today().month)
    selected_types = body.get("payment_types", [])   # boşsa tüm türler
    overwrite = body.get("overwrite", False)

    events = get_events_for_month(year, month)
    if selected_types:
        events = [e for e in events if e["payment_type"] in selected_types]

    if not events:
        return {"ok": True, "created": 0, "skipped": 0, "message": "Bu ay için seçili türde vergi olayı yok."}

    clients = db.query(Client).filter(Client.is_active == True).all()
    created = 0
    skipped = 0

    for client in clients:
        for ev in events:
            pt = PaymentType(ev["payment_type"])
            due = ev["due_date"]

            # Muhasebe ücreti tipi müşteriye özel — bu endpoint müşteri bazlı vergi içindir
            if pt == PaymentType.muhasebe:
                skipped += 1
                continue

            # Aynı ödeme zaten var mı?
            exists = db.query(Payment).filter(
                Payment.client_id    == client.id,
                Payment.payment_type == pt,
                Payment.due_date     == due,
            ).first()

            if exists:
                if overwrite:
                    db.delete(exists)
                    db.flush()
                else:
                    skipped += 1
                    continue

            p = Payment(
                client_id    = client.id,
                title        = f"{ev['name']} — {MONTH_NAMES_TR[month]} {year}",
                payment_type = pt,
                due_date     = due,
                status       = PaymentStatus.pending,
                is_personal  = False,
            )
            db.add(p)
            created += 1

    db.commit()
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "events_processed": len(events),
        "clients_processed": len(clients),
    }
