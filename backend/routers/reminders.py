from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
from database import get_db
from models import ReminderRule, ReminderLog, PaymentType
from routers.auth import require_auth

router = APIRouter()


# ── Kural CRUD ────────────────────────────────────────────

@router.get("/rules")
def list_rules(db: Session = Depends(get_db), _=Depends(require_auth)):
    rules = db.query(ReminderRule).order_by(ReminderRule.days_before).all()
    return [_rule_out(r) for r in rules]


@router.post("/rules")
def create_rule(body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    days = int(body.get("days_before", 0))
    if days < 0:
        raise HTTPException(400, "days_before 0 veya pozitif olmalı")
    pt = body.get("payment_type") or None
    rule = ReminderRule(
        name=body.get("name") or f"{days} Gün Önce",
        days_before=days,
        payment_type=PaymentType(pt) if pt else None,
        is_active=body.get("is_active", True),
        message_tpl=body.get("message_tpl"),
    )
    db.add(rule); db.commit(); db.refresh(rule)
    return _rule_out(rule)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    rule = db.query(ReminderRule).filter(ReminderRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Kural bulunamadı")
    for field in ("name", "days_before", "is_active", "message_tpl"):
        if field in body:
            setattr(rule, field, body[field])
    if "payment_type" in body:
        pt = body["payment_type"]
        rule.payment_type = PaymentType(pt) if pt else None
    db.commit(); db.refresh(rule)
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    rule = db.query(ReminderRule).filter(ReminderRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Kural bulunamadı")
    db.delete(rule); db.commit()
    return {"ok": True}


# ── Hatırlatma Logları ────────────────────────────────────

@router.get("/logs")
def list_logs(
    limit: int = 100,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    q = db.query(ReminderLog)
    if status:
        q = q.filter(ReminderLog.status == status)
    logs = q.order_by(ReminderLog.scheduled_at.desc()).limit(limit).all()
    return [_log_out(l) for l in logs]


# ── Durum Bilgisi ────────────────────────────────────────

@router.get("/status")
def get_status(db: Session = Depends(get_db), _=Depends(require_auth)):
    """Son kontrol tarihi ve bekleyen log sayısını döndür."""
    from models import AppSettings, ReminderLog
    import datetime
    settings = db.query(AppSettings).filter(AppSettings.id == 1).first()
    today = datetime.date.today()
    pending = db.query(ReminderLog).filter(ReminderLog.status == "logged").count()
    missed_days = 0
    last_check = None
    if settings and settings.last_reminder_check:
        last_check = str(settings.last_reminder_check)
        delta = today - settings.last_reminder_check
        missed_days = max(0, delta.days - 1)  # bugün hariç
    return {
        "last_check": last_check,
        "today": str(today),
        "missed_days": missed_days,
        "pending_logs": pending,
    }


# ── Manuel Tetikleme (test için) ──────────────────────────

@router.post("/run-now")
def run_now(
    body: Optional[dict] = None,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """Scheduler'ı hemen çalıştır — missed days telafisi dahil."""
    from scheduler import check_and_send_reminders
    import datetime
    force_range = None
    if body and body.get("from_date") and body.get("to_date"):
        force_range = (
            datetime.date.fromisoformat(body["from_date"]),
            datetime.date.fromisoformat(body["to_date"]),
        )
    sent, skipped, errors = check_and_send_reminders(db, force_date_range=force_range)
    return {"ok": True, "sent": sent, "skipped": skipped, "errors": errors}


# ── Yardımcılar ───────────────────────────────────────────

def _rule_out(r: ReminderRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "days_before": r.days_before,
        "payment_type": r.payment_type.value if r.payment_type else None,
        "is_active": r.is_active,
        "message_tpl": r.message_tpl,
        "created_at": r.created_at.isoformat(),
    }


def _log_out(l: ReminderLog) -> dict:
    return {
        "id": l.id,
        "rule_id": l.rule_id,
        "payment_id": l.payment_id,
        "client_id": l.client_id,
        "client_name": l.client.full_name if l.client else None,
        "phone": l.phone,
        "message_body": l.message_body,
        "status": l.status,
        "scheduled_at": l.scheduled_at.isoformat() if l.scheduled_at else None,
        "sent_at": l.sent_at.isoformat() if l.sent_at else None,
        "error_msg": l.error_msg,
        "created_at": l.created_at.isoformat(),
    }
