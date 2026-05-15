"""
Scheduler — Uygulama her acildiginda calisir.
Son kontrol tarihinden buguye kadar gecen tum gunleri tarar,
kacirilan hafta sonu / tatil gunlerini de telafi eder.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Tuple

log = logging.getLogger(__name__)


def check_and_send_reminders(db, force_date_range=None) -> Tuple[int, int, int]:
    """
    Aktif hatirlatma kurallarini tara, vadelere gore log olustur.
    force_date_range: (start_date, end_date) — test icin belirli aralik
    Returns: (sent, skipped, errors)
    """
    from models import ReminderRule, ReminderLog, Payment, PaymentStatus, AppSettings

    today = date.today()
    sent = skipped = errors = 0

    # Son kontrol tarihini oku
    settings = db.query(AppSettings).filter(AppSettings.id == 1).first()
    if not settings:
        log.warning("AppSettings bulunamadi.")
        return 0, 0, 0

    if force_date_range:
        scan_start, scan_end = force_date_range
    else:
        last_check = settings.last_reminder_check
        if last_check is None:
            # Hic calistirilmamis — sadece bugun
            scan_start = today
        elif last_check >= today:
            # Bugün zaten çalışmış
            log.info("Hatirlatma bugün zaten calistirildi, atlanıyor.")
            return 0, 0, 0
        else:
            # Son kontrol Cuma, bugun Pazartesi — aradaki tum gunleri tara
            scan_start = last_check + timedelta(days=1)
        scan_end = today

    rules = db.query(ReminderRule).filter(ReminderRule.is_active == True).all()
    if not rules:
        log.info("Aktif hatirlatma kurali yok.")
        _update_last_check(db, settings, today)
        return 0, 0, 0

    log.info(f"Hatirlatma taraniyor: {scan_start} - {scan_end} ({(scan_end - scan_start).days + 1} gun)")

    # Tarih araligindaki her gunu tara
    current = scan_start
    while current <= scan_end:
        for rule in rules:
            target_date = current + timedelta(days=rule.days_before)

            # Vadesi gecmis odemeler icin hatirlatma gonderme
            if target_date < today:
                skipped += 1
                continue

            q = db.query(Payment).filter(
                Payment.due_date  == target_date,
                Payment.status    == PaymentStatus.pending,
                Payment.client_id != None,
            )
            if rule.payment_type:
                q = q.filter(Payment.payment_type == rule.payment_type)

            payments = q.all()

            for payment in payments:
                if not payment.client or not payment.client.phone:
                    skipped += 1
                    continue

                # Bu kural + odeme icin hic log yazilmamis mi? (tarih fark etmez)
                already = db.query(ReminderLog).filter(
                    ReminderLog.rule_id    == rule.id,
                    ReminderLog.payment_id == payment.id,
                    ReminderLog.status.notin_(["failed"]),
                ).first()

                if already:
                    skipped += 1
                    continue

                from routers.whatsapp import format_amount
                client = payment.client
                pt_label = {
                    "kdv": "KDV", "stopaj": "Stopaj/Muhtasar",
                    "muhasebe_ucreti": "Muhasebe Ucreti",
                    "gelir_vergisi": "Gelir Vergisi", "gecici_vergi": "Gecici Vergi",
                    "sgk": "SGK Primi", "damga_vergisi": "Damga Vergisi",
                    "motorlu_tasit_vergisi": "MTV", "otv": "OTV",
                }.get(payment.payment_type.value if payment.payment_type else "", "Odeme")

                tutar = format_amount(float(payment.amount)) + " TL" if payment.amount else "-"
                tarih = target_date.strftime("%d.%m.%Y")

                # Kac gun kaldi?
                days_left = (target_date - today).days
                if days_left == 0:
                    zaman = "bugün"
                elif days_left == 1:
                    zaman = "yarın"
                else:
                    zaman = f"{days_left} gün sonra"

                msg = (
                    f"Sayin {client.full_name}, {tarih} tarihinde ({zaman}) "
                    f"odenmesi gereken {pt_label} borcunuz {tutar}'dir. Iyi gunler."
                )

                reminder_log = ReminderLog(
                    rule_id      = rule.id,
                    payment_id   = payment.id,
                    client_id    = client.id,
                    phone        = client.phone,
                    message_body = msg,
                    status       = "logged",
                    scheduled_at = datetime.now(timezone.utc),
                )
                db.add(reminder_log)
                sent += 1

        current += timedelta(days=1)

    db.commit()
    _update_last_check(db, settings, today)

    log.info(f"Hatirlatma tamamlandi: {sent} log, {skipped} atlandi, {errors} hata.")
    return sent, skipped, errors


def _update_last_check(db, settings, today: date):
    settings.last_reminder_check = today
    db.commit()


def run_on_startup(app=None):
    """
    Uygulama acildiginda cagrilir.
    Son kontrol tarihinden buguye kadar gecen gunleri tarar.
    """
    try:
        from database import SessionLocal
        db = SessionLocal()
        try:
            sent, skipped, errors = check_and_send_reminders(db)
            log.info(f"Acilis hatirlatma taramasi: {sent} log, {skipped} atlandi.")
        finally:
            db.close()
    except Exception as e:
        log.error(f"Acilis scheduler hatasi: {e}")


def start_scheduler(app=None):
    """
    APScheduler ile gunluk 08:30 kontrolu + acilis kontrolu.
    APScheduler yuklu degilse sadece acilis kontrolu calisiyor.
    """
    # Her halukarda acilis taramasi yap (hafta sonu telafisi dahil)
    run_on_startup()

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from database import SessionLocal

        def job():
            db = SessionLocal()
            try:
                check_and_send_reminders(db)
            except Exception as e:
                log.error(f"Scheduler hatasi: {e}")
            finally:
                db.close()

        scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
        scheduler.add_job(
            job,
            trigger=CronTrigger(hour=8, minute=30),
            id="daily_reminders",
            replace_existing=True,
        )
        scheduler.start()
        log.info("Scheduler baslatildi — her gun 08:30 + acilis taramasi aktif.")
        return scheduler

    except ImportError:
        log.warning("APScheduler yuklu degil — sadece acilis taramasi aktif.")
        return None
    except Exception as e:
        log.error(f"Scheduler baslatılamadi: {e}")
        return None
