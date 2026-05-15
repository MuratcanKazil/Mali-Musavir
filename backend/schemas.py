from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
from models import PaymentType, WhatsappStatus, PaymentStatus


class ClientCreate(BaseModel):
    full_name:   str
    phone:       str
    email:       Optional[str] = None
    tax_number:  Optional[str] = None
    tax_type:    Optional[str] = None
    monthly_fee: Optional[Decimal] = None
    fee_due_day: int = 5
    notes:       Optional[str] = None

class ClientUpdate(ClientCreate):
    full_name: Optional[str] = None
    phone:     Optional[str] = None

class ClientOut(ClientCreate):
    id:         str
    is_active:  bool
    balance:    Optional[Decimal] = Decimal("0")
    created_at: datetime
    class Config:
        from_attributes = True


class PaymentCreate(BaseModel):
    client_id:     Optional[str] = None
    title:         str
    payment_type:  PaymentType
    amount:        Optional[Decimal] = None
    due_date:      date
    is_personal:   bool = False
    recurrence:    Optional[str] = None
    from_tahakkuk: bool = False

class PaymentUpdate(BaseModel):
    title:         Optional[str] = None
    amount:        Optional[Decimal] = None
    due_date:      Optional[date] = None
    status:        Optional[PaymentStatus] = None
    reminder_sent: Optional[bool] = None

class PaymentOut(PaymentCreate):
    id:             str
    status:         PaymentStatus
    reminder_sent:  bool
    paid_at:        Optional[datetime] = None
    created_at:     datetime
    client_name:    Optional[str] = None
    client_phone:   Optional[str] = None
    class Config:
        from_attributes = True

class CalendarEventOut(BaseModel):
    id:           str
    title:        str
    due_date:     date
    payment_type: PaymentType
    amount:       Optional[Decimal] = None
    status:       PaymentStatus
    is_personal:  bool
    client_id:    Optional[str] = None
    client_name:  Optional[str] = None
    client_phone: Optional[str] = None


class TemplateCreate(BaseModel):
    name:         str
    payment_type: Optional[PaymentType] = None
    body:         str
    is_default:   bool = False

class TemplateOut(TemplateCreate):
    id:         str
    created_at: datetime
    class Config:
        from_attributes = True


class WhatsappLinkRequest(BaseModel):
    client_id:      str
    template_id:    Optional[str] = None
    amount:         Optional[float] = None
    due_date:       Optional[str] = None
    custom_message: Optional[str] = None

class WhatsappLogOut(BaseModel):
    id:           str
    client_id:    Optional[str]
    phone:        str
    message_body: str
    payment_type: Optional[str]
    status:       WhatsappStatus
    sent_at:      datetime
    client_name:  Optional[str] = None
    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_clients:             int
    active_clients:            int
    pending_payments:          int
    overdue_payments:          int
    whatsapp_sent_this_month:  int
    total_balance:             Decimal
    upcoming_3days:            List[PaymentOut]
