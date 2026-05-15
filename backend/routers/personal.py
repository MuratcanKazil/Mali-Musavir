from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import date
import calendar as cal_mod
from database import get_db
from models import Payment, PaymentType, PaymentStatus
from routers.auth import require_auth
from routers.payments import _enrich, _next_due_date

router = APIRouter()

PERSONAL_CATEGORIES = [
    "Kira", "Fatura", "Market", "Araç", "Yakıt",
    "Sağlık", "Sigorta", "Vergi", "SGK (Kişisel)",
    "Kredi / Borç", "Eğitim", "Yemek / Restoran",
    "Danışmanlık Geliri", "Kira Geliri", "Diğer Gelir", "Diğer",
]


@router.get("/categories")
def get_categories(_=Depends(require_auth)):
    return PERSONAL_CATEGORIES


@router.get("/")
def list_personal(
    month:     Optional[str] = None,   # "2025-04"
    direction: Optional[str] = None,   # "income" | "expense"
    category:  Optional[str] = None,
    db:        Session = Depends(get_db),
    _=Depends(require_auth),
):
    q = db.query(Payment).filter(Payment.is_personal == True)

    if month:
        try:
            year, m = int(month.split("-")[0]), int(month.split("-")[1])
            month_start = date(year, m, 1)
            last_day    = cal_mod.monthrange(year, m)[1]
            month_end   = date(year, m, last_day)
            q = q.filter(Payment.due_date >= month_start, Payment.due_date <= month_end)
        except Exception:
            pass

    if direction: q = q.filter(Payment.direction == direction)
    if category:  q = q.filter(Payment.category  == category)

    rows = q.order_by(Payment.due_date.desc()).all()
    return [_row(p) for p in rows]


@router.get("/summary")
def personal_summary(
    month: Optional[str] = None,   # "2025-04" — yoksa mevcut ay
    db:    Session = Depends(get_db),
    _=Depends(require_auth),
):
    today = date.today()
    if month:
        try:
            year, m = int(month.split("-")[0]), int(month.split("-")[1])
        except Exception:
            year, m = today.year, today.month
    else:
        year, m = today.year, today.month

    month_start = date(year, m, 1)
    last_day    = cal_mod.monthrange(year, m)[1]
    month_end   = date(year, m, last_day)

    base = db.query(Payment).filter(
        Payment.is_personal == True,
        Payment.due_date >= month_start,
        Payment.due_date <= month_end,
    )

    total_income = db.query(func.sum(Payment.amount)).filter(
        Payment.is_personal == True,
        Payment.direction   == "income",
        Payment.due_date    >= month_start,
        Payment.due_date    <= month_end,
    ).scalar() or 0

    total_expense = db.query(func.sum(Payment.amount)).filter(
        Payment.is_personal == True,
        Payment.direction   == "expense",
        Payment.due_date    >= month_start,
        Payment.due_date    <= month_end,
    ).scalar() or 0

    paid_expense = db.query(func.sum(Payment.amount)).filter(
        Payment.is_personal == True,
        Payment.direction   == "expense",
        Payment.status      == PaymentStatus.paid,
        Payment.due_date    >= month_start,
        Payment.due_date    <= month_end,
    ).scalar() or 0

    pending_expense = db.query(func.sum(Payment.amount)).filter(
        Payment.is_personal == True,
        Payment.direction   == "expense",
        Payment.status      != PaymentStatus.paid,
        Payment.due_date    >= month_start,
        Payment.due_date    <= month_end,
    ).scalar() or 0

    # Kategoriye göre gider dağılımı
    cat_rows = db.query(Payment.category, func.sum(Payment.amount)).filter(
        Payment.is_personal == True,
        Payment.direction   == "expense",
        Payment.due_date    >= month_start,
        Payment.due_date    <= month_end,
    ).group_by(Payment.category).all()

    return {
        "year": year, "month": m,
        "total_income":   float(total_income),
        "total_expense":  float(total_expense),
        "paid_expense":   float(paid_expense),
        "pending_expense": float(pending_expense),
        "net":            float(total_income) - float(total_expense),
        "category_breakdown": [
            {"category": r[0] or "Diğer", "amount": float(r[1])}
            for r in sorted(cat_rows, key=lambda x: x[1], reverse=True)
        ],
    }


@router.post("/")
def create_personal(body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    from models import new_uuid
    required = ["title", "amount", "due_date"]
    for f in required:
        if not body.get(f):
            raise HTTPException(400, f"{f} zorunlu")

    p = Payment(
        client_id    = None,
        title        = body["title"],
        payment_type = PaymentType.kisisel,
        amount       = float(body["amount"]),
        due_date     = date.fromisoformat(body["due_date"]),
        status       = PaymentStatus.pending,
        is_personal  = True,
        direction    = body.get("direction", "expense"),
        category     = body.get("category", "Diğer"),
        recurrence   = body.get("recurrence") or None,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _row(p)


@router.post("/{payment_id}/mark-paid")
def mark_paid_personal(payment_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    from datetime import datetime
    p = db.query(Payment).filter(
        Payment.id == payment_id, Payment.is_personal == True
    ).first()
    if not p:
        raise HTTPException(404, "Kayıt bulunamadı")

    p.status  = PaymentStatus.paid
    p.paid_at = datetime.utcnow()
    db.commit()

    # Tekrar eden kişisel ödeme
    if p.recurrence in ("monthly", "yearly"):
        next_due = _next_due_date(p.due_date, p.recurrence)
        already = db.query(Payment).filter(
            Payment.title      == p.title,
            Payment.is_personal == True,
            Payment.due_date   == next_due,
        ).first()
        if not already:
            db.add(Payment(
                client_id    = None,
                title        = p.title,
                payment_type = p.payment_type,
                amount       = p.amount,
                due_date     = next_due,
                status       = PaymentStatus.pending,
                is_personal  = True,
                direction    = p.direction,
                category     = p.category,
                recurrence   = p.recurrence,
            ))
            db.commit()

    return {"ok": True}


@router.delete("/{payment_id}")
def delete_personal(payment_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    p = db.query(Payment).filter(
        Payment.id == payment_id, Payment.is_personal == True
    ).first()
    if not p:
        raise HTTPException(404, "Kayıt bulunamadı")
    db.delete(p)
    db.commit()
    return {"ok": True}


def _row(p: Payment) -> dict:
    return {
        "id":          p.id,
        "title":       p.title,
        "amount":      float(p.amount) if p.amount else 0,
        "due_date":    str(p.due_date),
        "status":      p.status.value,
        "direction":   p.direction or "expense",
        "category":    p.category or "Diğer",
        "recurrence":  p.recurrence,
        "paid_at":     p.paid_at.isoformat() if p.paid_at else None,
    }
