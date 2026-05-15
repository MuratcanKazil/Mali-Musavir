from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, timedelta, datetime
import calendar as cal_mod
from database import get_db
from models import Payment, Client, PaymentStatus, PaymentType
from schemas import PaymentCreate, PaymentUpdate, PaymentOut, CalendarEventOut

router = APIRouter()


def _enrich(p: Payment) -> dict:
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    d["client_name"]  = p.client.full_name if p.client else None
    d["client_phone"] = p.client.phone     if p.client else None
    return d


def _next_due_date(current: date, recurrence: str) -> date:
    """Tekrar türüne göre bir sonraki vade tarihini hesapla."""
    if recurrence == "monthly":
        month = current.month + 1
        year  = current.year
        if month > 12:
            month = 1
            year += 1
        last_day = cal_mod.monthrange(year, month)[1]
        day = min(current.day, last_day)
        return date(year, month, day)
    elif recurrence == "yearly":
        try:
            return current.replace(year=current.year + 1)
        except ValueError:
            # 29 Şubat → 28 Şubat
            return current.replace(year=current.year + 1, day=28)
    return current


def ensure_monthly_fee_payment(client: Client, db: Session, target_month: date = None):
    """
    Müşterinin hedef ay için muhasebe ücreti payment kaydı yoksa oluştur.
    target_month verilmezse mevcut ayı kullanır.
    """
    if not client.monthly_fee or not client.is_active:
        return

    if target_month is None:
        target_month = date.today()

    month_start = target_month.replace(day=1)
    last_day    = cal_mod.monthrange(target_month.year, target_month.month)[1]
    month_end   = target_month.replace(day=last_day)

    # Bu ay için zaten muhasebe_ucreti kaydı var mı?
    existing = db.query(Payment).filter(
        Payment.client_id    == client.id,
        Payment.payment_type == PaymentType.muhasebe,
        Payment.due_date     >= month_start,
        Payment.due_date     <= month_end,
    ).first()

    if existing:
        return  # Zaten var, tekrar oluşturma

    due_day  = min(client.fee_due_day or 5, last_day)
    due_date = target_month.replace(day=due_day)

    p = Payment(
        client_id    = client.id,
        title        = f"Muhasebe Ücreti — {target_month.strftime('%B %Y')}",
        payment_type = PaymentType.muhasebe,
        amount       = float(client.monthly_fee),
        due_date     = due_date,
        status       = PaymentStatus.pending,
        recurrence   = "monthly",
        is_personal  = False,
    )
    db.add(p)
    db.commit()


@router.get("/", response_model=List[PaymentOut])
def list_payments(
    status:       Optional[str]  = None,
    is_personal:  Optional[bool] = None,
    client_id:    Optional[str]  = None,
    payment_type: Optional[str]  = None,
    date_from:    Optional[date] = None,
    date_to:      Optional[date] = None,
    db:           Session        = Depends(get_db),
):
    q = db.query(Payment)
    if status:                  q = q.filter(Payment.status == status)
    if is_personal is not None: q = q.filter(Payment.is_personal == is_personal)
    if client_id:               q = q.filter(Payment.client_id == client_id)
    if payment_type:            q = q.filter(Payment.payment_type == payment_type)
    if date_from:               q = q.filter(Payment.due_date >= date_from)
    if date_to:                 q = q.filter(Payment.due_date <= date_to)
    payments = q.order_by(Payment.due_date).all()
    return [PaymentOut(**_enrich(p)) for p in payments]


@router.get("/calendar/events", response_model=List[CalendarEventOut])
def calendar_events(
    start: Optional[date] = None,
    end:   Optional[date] = None,
    db:    Session        = Depends(get_db),
):
    q = db.query(Payment)
    if start: q = q.filter(Payment.due_date >= start)
    if end:   q = q.filter(Payment.due_date <= end)
    payments = q.order_by(Payment.due_date).all()
    return [CalendarEventOut(
        id=str(p.id), title=p.title, due_date=p.due_date,
        payment_type=p.payment_type, amount=p.amount,
        status=p.status, is_personal=p.is_personal,
        client_id=str(p.client_id) if p.client_id else None,
        client_name=p.client.full_name  if p.client else None,
        client_phone=p.client.phone     if p.client else None,
    ) for p in payments]


@router.get("/upcoming/dashboard")
def upcoming_payments(days: int = 7, db: Session = Depends(get_db)):
    today  = date.today()
    future = today + timedelta(days=days)
    payments = (db.query(Payment)
                .filter(Payment.due_date >= today,
                        Payment.due_date <= future,
                        Payment.status == PaymentStatus.pending)
                .order_by(Payment.due_date)
                .all())
    return [PaymentOut(**_enrich(p)) for p in payments]


@router.post("/", response_model=PaymentOut)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db)):
    if data.client_id:
        if not db.query(Client).filter(Client.id == data.client_id).first():
            raise HTTPException(404, "Müşteri bulunamadı")
    p = Payment(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return PaymentOut(**_enrich(p))


@router.put("/{payment_id}", response_model=PaymentOut)
def update_payment(payment_id: str, data: PaymentUpdate, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404, "Ödeme bulunamadı")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return PaymentOut(**_enrich(p))


@router.post("/{payment_id}/mark-paid")
def mark_paid(payment_id: str, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404, "Ödeme bulunamadı")

    p.status  = PaymentStatus.paid
    p.paid_at = datetime.utcnow()
    db.commit()

    # ── Tekrar eden ödeme: bir sonraki kaydı oluştur ──────────────────
    if p.recurrence in ("monthly", "yearly"):
        next_due = _next_due_date(p.due_date, p.recurrence)

        # Aynı tarihte zaten aynı başlıkla kayıt var mı? (idempotency)
        already = db.query(Payment).filter(
            Payment.client_id    == p.client_id,
            Payment.title        == p.title,
            Payment.payment_type == p.payment_type,
            Payment.due_date     == next_due,
        ).first()

        if not already:
            next_p = Payment(
                client_id    = p.client_id,
                title        = p.title,
                payment_type = p.payment_type,
                amount       = p.amount,
                due_date     = next_due,
                status       = PaymentStatus.pending,
                is_personal  = p.is_personal,
                recurrence   = p.recurrence,
            )
            db.add(next_p)
            db.commit()

    return {"ok": True}


@router.delete("/{payment_id}")
def delete_payment(payment_id: str, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        raise HTTPException(404, "Ödeme bulunamadı")
    db.delete(p)
    db.commit()
    return {"ok": True}

