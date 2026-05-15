from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import date, timedelta
from decimal import Decimal
from database import get_db
from models import Client, Payment, WhatsappLog, PaymentStatus, PaymentType
from schemas import DashboardStats, PaymentOut

router = APIRouter()

def _enrich(p):
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    d["client_name"]  = p.client.full_name if p.client else None
    d["client_phone"] = p.client.phone     if p.client else None
    return d

@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(db: Session = Depends(get_db)):
    today       = date.today()
    in3         = today + timedelta(days=3)
    month_start = today.replace(day=1)

    total_clients    = db.query(Client).count()
    active_clients   = db.query(Client).filter(Client.is_active == True).count()
    pending_payments = db.query(Payment).filter(Payment.status == PaymentStatus.pending).count()
    overdue_payments = db.query(Payment).filter(
        Payment.status == PaymentStatus.pending,
        Payment.due_date < today
    ).count()
    wa_this_month = db.query(WhatsappLog).filter(
        WhatsappLog.sent_at >= month_start
    ).count()

    # Toplam birikmiş bakiye
    total_balance_row = db.query(func.sum(Client.balance)).filter(
        Client.is_active == True
    ).scalar()
    total_balance = Decimal(str(total_balance_row or 0))

    upcoming = (db.query(Payment)
                .filter(Payment.due_date >= today, Payment.due_date <= in3,
                        Payment.status == PaymentStatus.pending)
                .order_by(Payment.due_date).limit(10).all())

    return DashboardStats(
        total_clients=total_clients,
        active_clients=active_clients,
        pending_payments=pending_payments,
        overdue_payments=overdue_payments,
        whatsapp_sent_this_month=wa_this_month,
        total_balance=total_balance,
        upcoming_3days=[PaymentOut(**_enrich(p)) for p in upcoming],
    )


@router.get("/monthly-chart")
def monthly_chart(db: Session = Depends(get_db)):
    """Son 12 ay için aylık muhasebe geliri ve toplam ödeme tutarı"""
    today = date.today()
    months = []
    for i in range(11, -1, -1):
        # i ay öncesi
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        months.append((year, month))

    result = []
    for year, month in months:
        import calendar as cal_mod
        month_start = date(year, month, 1)
        last_day = cal_mod.monthrange(year, month)[1]
        month_end = date(year, month, last_day)

        # Ödenen muhasebe ücretleri (paid, muhasebe_ucreti türü, müşteriye ait)
        muhasebe_paid = db.query(func.sum(Payment.amount)).filter(
            Payment.payment_type == PaymentType.muhasebe,
            Payment.status == PaymentStatus.paid,
            Payment.client_id != None,
            Payment.is_personal == False,
            Payment.paid_at >= month_start,
            Payment.paid_at <= month_end,
        ).scalar() or 0

        # O ay vadesi gelen ve ödenmemiş müşteri ödemeleri (kişisel hariç)
        total_pending = db.query(func.sum(Payment.amount)).filter(
            Payment.due_date >= month_start,
            Payment.due_date <= month_end,
            Payment.status != PaymentStatus.paid,
            Payment.client_id != None,
            Payment.is_personal == False,
        ).scalar() or 0

        # Hedef Ciro: o dönemde aktif müşterilerin toplam aylık ücretleri
        hedef_ciro = db.query(func.sum(Client.monthly_fee)).filter(
            Client.is_active == True,
            Client.monthly_fee != None,
        ).scalar() or 0

        # Kişisel giderler: is_personal=True olan ödemeler (vadesi o ay)
        giderler = db.query(func.sum(Payment.amount)).filter(
            Payment.due_date >= month_start,
            Payment.due_date <= month_end,
            Payment.is_personal == True,
        ).scalar() or 0

        label = f"{month:02d}/{str(year)[2:]}"
        result.append({
            "label":           label,
            "muhasebe_geliri": float(muhasebe_paid),
            "bekleyen_alacak": float(total_pending),
            "hedef_ciro":      float(hedef_ciro),
            "giderler":        float(giderler),
        })

    return result

