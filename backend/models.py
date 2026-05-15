from sqlalchemy import (
    Column, String, Numeric, Integer, Boolean,
    Date, DateTime, Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from database import Base


def new_uuid():
    return str(uuid.uuid4())


class PaymentType(str, enum.Enum):
    # Orijinal türler
    kdv            = "kdv"
    stopaj         = "stopaj"
    muhasebe       = "muhasebe_ucreti"
    gelir_vergisi  = "gelir_vergisi"
    gecici_vergi   = "gecici_vergi"
    sgk            = "sgk"
    kisisel        = "kisisel"
    # Tahakkuk fişinden gelen yeni türler
    damga_vergisi  = "damga_vergisi"
    ozel_iletisim  = "ozel_iletisim_vergisi"
    otv            = "otv"
    motorlu_tasit  = "motorlu_tasit_vergisi"


class WhatsappStatus(str, enum.Enum):
    pending  = "pending"
    opened   = "opened"
    failed   = "failed"


class PaymentStatus(str, enum.Enum):
    pending  = "pending"
    paid     = "paid"
    overdue  = "overdue"


class AppSettings(Base):
    __tablename__ = "app_settings"
    id                   = Column(Integer, primary_key=True, default=1)
    username             = Column(String(100), nullable=False, default="admin")
    password_hash        = Column(String(64), nullable=False)
    wa_phone_number_id   = Column(String(100))
    wa_access_token      = Column(String(500))
    wa_template_name     = Column(String(100), default="odeme_hatirlatma")
    wa_template_lang     = Column(String(10),  default="tr")
    last_reminder_check  = Column(Date)   # Son başarılı scheduler çalışma tarihi


class Client(Base):
    __tablename__ = "clients"
    id            = Column(String(36), primary_key=True, default=new_uuid)
    full_name     = Column(String(200), nullable=False)
    phone         = Column(String(20), nullable=False)
    email         = Column(String(200))
    tax_number    = Column(String(20))
    tax_type      = Column(String(100))
    monthly_fee   = Column(Numeric(10, 2))
    fee_due_day   = Column(Integer, default=5)
    is_active     = Column(Boolean, default=True)
    notes         = Column(Text)
    # Bakiye: ödenmemiş muhasebe ücretleri birikir
    balance       = Column(Numeric(10, 2), default=0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    payments      = relationship("Payment", back_populates="client", cascade="all, delete-orphan")
    whatsapp_logs = relationship("WhatsappLog", back_populates="client")
    transactions  = relationship("ClientTransaction", back_populates="client", cascade="all, delete-orphan")
    documents     = relationship("ClientDocument", back_populates="client", cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"
    id              = Column(String(36), primary_key=True, default=new_uuid)
    client_id       = Column(String(36), ForeignKey("clients.id"), nullable=True)
    title           = Column(String(200), nullable=False)
    payment_type    = Column(SAEnum(PaymentType), nullable=False)
    amount          = Column(Numeric(10, 2))
    due_date        = Column(Date, nullable=False)
    paid_at         = Column(DateTime(timezone=True))
    status          = Column(SAEnum(PaymentStatus), default=PaymentStatus.pending)
    reminder_sent   = Column(Boolean, default=False)
    is_personal     = Column(Boolean, default=False)
    recurrence      = Column(String(20))
    # Kişisel finans alanları
    direction       = Column(String(10), default="expense")   # "income" | "expense"
    category        = Column(String(100))                      # Kira, Fatura, Vergi, Araç...
    # Tahakkuk fişinden mi geldi?
    from_tahakkuk   = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    client          = relationship("Client", back_populates="payments")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id         = Column(String(36), primary_key=True, default=new_uuid)
    title      = Column(String(200), nullable=False)
    date       = Column(Date, nullable=False)
    notes      = Column(Text)
    color      = Column(String(20), default="#3949AB")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    id           = Column(String(36), primary_key=True, default=new_uuid)
    name         = Column(String(100), nullable=False)
    payment_type = Column(SAEnum(PaymentType))
    body         = Column(Text, nullable=False)
    is_default   = Column(Boolean, default=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class ClientTransaction(Base):
    """Müşteri hareketleri — borç eklendi / tahsilat yapıldı log kaydı"""
    __tablename__ = "client_transactions"
    id          = Column(String(36), primary_key=True, default=new_uuid)
    client_id   = Column(String(36), ForeignKey("clients.id"), nullable=False)
    tx_type     = Column(String(20), nullable=False)   # "debit" | "credit"
    amount      = Column(Numeric(10, 2), nullable=False)
    description = Column(String(300))
    tx_date     = Column(Date, nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    client      = relationship("Client", back_populates="transactions")


class ClientDocument(Base):
    """Müşteri evrakları — vergi levhası, imza sirküleri vb."""
    __tablename__ = "client_documents"
    id          = Column(String(36), primary_key=True, default=new_uuid)
    client_id   = Column(String(36), ForeignKey("clients.id"), nullable=False)
    doc_name    = Column(String(200), nullable=False)
    file_path   = Column(String(500))
    expiry_date = Column(Date)
    notes       = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    client      = relationship("Client", back_populates="documents")


class WhatsappLog(Base):
    __tablename__ = "whatsapp_logs"
    id           = Column(String(36), primary_key=True, default=new_uuid)
    client_id    = Column(String(36), ForeignKey("clients.id"), nullable=True)
    phone        = Column(String(20), nullable=False)
    message_body = Column(Text, nullable=False)
    payment_type = Column(String(50))
    status       = Column(SAEnum(WhatsappStatus), default=WhatsappStatus.opened)
    sent_at      = Column(DateTime(timezone=True), server_default=func.now())
    client       = relationship("Client", back_populates="whatsapp_logs")


# ── Hatırlatma Kuralları ──────────────────────────────────
class ReminderRule(Base):
    """Kullanıcının tanımladığı otomatik WA hatırlatma kuralları."""
    __tablename__ = "reminder_rules"
    id           = Column(String(36), primary_key=True, default=new_uuid)
    name         = Column(String(100), nullable=False)          # Örn: "3 Gün Önce"
    days_before  = Column(Integer, nullable=False)              # Vade - N gün
    payment_type = Column(SAEnum(PaymentType), nullable=True)   # None = tüm türler
    is_active    = Column(Boolean, default=True)
    message_tpl  = Column(Text)                                 # Şimdilik boş, ileride şablon
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class ReminderLog(Base):
    """Gönderilen / gönderilmesi gereken hatırlatmaların kaydı."""
    __tablename__ = "reminder_logs"
    id           = Column(String(36), primary_key=True, default=new_uuid)
    rule_id      = Column(String(36), ForeignKey("reminder_rules.id"), nullable=True)
    payment_id   = Column(String(36), ForeignKey("payments.id"),       nullable=True)
    client_id    = Column(String(36), ForeignKey("clients.id"),        nullable=True)
    phone        = Column(String(20))
    message_body = Column(Text)
    status       = Column(String(20), default="pending")  # pending | sent | failed | skipped
    scheduled_at = Column(DateTime(timezone=True))
    sent_at      = Column(DateTime(timezone=True))
    error_msg    = Column(Text)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    rule         = relationship("ReminderRule", foreign_keys=[rule_id])
    payment      = relationship("Payment",       foreign_keys=[payment_id])
    client       = relationship("Client",        foreign_keys=[client_id])


# ── Vergi Takvimi ─────────────────────────────────────────
class TaxEvent(Base):
    """Türkiye vergi takvimindeki sabit/dönemsel vergi olayları."""
    __tablename__ = "tax_events"
    id              = Column(String(36), primary_key=True, default=new_uuid)
    name            = Column(String(200), nullable=False)   # "KDV Beyannamesi"
    payment_type    = Column(SAEnum(PaymentType), nullable=True)
    month           = Column(Integer, nullable=False)        # 1-12 (hangi ayda vadesi geliyor)
    day             = Column(Integer, nullable=False)        # Ayın kaçında
    applies_to      = Column(String(50), default="all")     # "all" | "quarterly" | "annual"
    quarter_months  = Column(String(50))                    # "1,4,7,10" gibi tetikleyen aylar
    description     = Column(Text)
    is_active       = Column(Boolean, default=True)
    auto_create     = Column(Boolean, default=False)        # Otomatik ödeme oluşturulsun mu
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
