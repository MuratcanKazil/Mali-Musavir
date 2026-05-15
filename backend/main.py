from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging, os, pathlib

logging.basicConfig(level=logging.INFO)

from database import engine, Base, SessionLocal
from routers import clients, payments, dashboard, auth, events
from routers import whatsapp, tahakkuk, personal, reminders, tax_calendar
from routers import luca, wa_incoming

Base.metadata.create_all(bind=engine)


def seed_templates():
    db = SessionLocal()
    try:
        from models import MessageTemplate, PaymentType
        if db.query(MessageTemplate).count() > 0:
            return
        templates = [
            MessageTemplate(name="KDV Hatırlatma", payment_type=PaymentType.kdv, is_default=True,
                body="Sayın [isim], [tarih] tarihinde ödenmesi gereken KDV borcunuz [tutar] TL'dir. Lütfen son ödeme tarihini gözden geçirin. İyi çalışmalar."),
            MessageTemplate(name="Muhasebe Ücreti", payment_type=PaymentType.muhasebe, is_default=True,
                body="Sayın [isim], [tarih] tarihine kadar ödenmesi gereken aylık muhasebe ücretiniz [tutar] TL'dir. Bilginize sunarız."),
            MessageTemplate(name="Stopaj Hatırlatma", payment_type=PaymentType.stopaj, is_default=True,
                body="Sayın [isim], [tarih] son ödeme tarihli stopaj borcunuz [tutar] TL'dir. Zamanında ödeme yapmanızı rica ederiz."),
            MessageTemplate(name="SGK Bildirimi", payment_type=PaymentType.sgk, is_default=True,
                body="Sayın [isim], [tarih] tarihine kadar SGK priminizin [tutar] TL olduğu hatırlatılır. İyi günler."),
            MessageTemplate(name="Genel Hatırlatma", payment_type=None, is_default=False,
                body="Sayın [isim], [tarih] tarihinde [tutar] TL [tür] ödemeniz bulunmaktadır. İyi günler."),
        ]
        for t in templates:
            db.add(t)
        db.commit()
        logging.info("Varsayılan mesaj taslakları oluşturuldu.")
    finally:
        db.close()


def seed_admin():
    db = SessionLocal()
    try:
        from models import AppSettings
        import hashlib, os
        if db.query(AppSettings).count() > 0:
            return
        s = AppSettings(
            username=os.getenv("ADMIN_USERNAME", "admin"),
            password_hash=hashlib.sha256(
                os.getenv("ADMIN_PASSWORD", "Mali2024!").encode()
            ).hexdigest(),
        )
        db.add(s)
        db.commit()
        logging.info("Admin hesabı oluşturuldu.")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_admin()
    seed_templates()
    from scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(title="Mali Müşavir API", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "null", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,       prefix="/api/auth",      tags=["Auth"])
app.include_router(clients.router,    prefix="/api/clients",   tags=["Müşteriler"])
app.include_router(payments.router,   prefix="/api/payments",  tags=["Ödemeler"])
app.include_router(personal.router,   prefix="/api/personal",  tags=["Kişisel Finans"])
app.include_router(whatsapp.router,   prefix="/api/whatsapp",  tags=["WhatsApp"])
app.include_router(dashboard.router,  prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(tahakkuk.router,  prefix="/api/tahakkuk",  tags=["Tahakkuk"])
app.include_router(events.router,     prefix="/api/events",    tags=["Takvim"])
app.include_router(reminders.router,  prefix="/api/reminders", tags=["Hatirlatmalar"])
app.include_router(tax_calendar.router, prefix="/api/tax-calendar", tags=["Vergi Takvimi"])
app.include_router(luca.router,       prefix="/api/luca",       tags=["Luca Export"])
app.include_router(wa_incoming.router, prefix="/api/wa-incoming", tags=["WA Gelen"])


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Mali Müşavir v4 (Desktop)"}


# ── Frontend'i serve et ───────────────────────────────────
FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"

@app.get("/")
def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")
