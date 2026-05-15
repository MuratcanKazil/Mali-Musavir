from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import date
from database import get_db
from models import Client, Payment, PaymentType, PaymentStatus, ClientTransaction, ClientDocument
from schemas import ClientCreate, ClientUpdate, ClientOut
from routers.auth import require_auth
import uuid
import calendar as cal_mod

router = APIRouter()


def _ensure_monthly_fee(client: Client, db: Session):
    """Müşteri eklenince/güncellenince bu aya ait muhasebe ücreti yoksa oluştur."""
    if not client.monthly_fee or not client.is_active:
        return
    today       = date.today()
    month_start = today.replace(day=1)
    last_day    = cal_mod.monthrange(today.year, today.month)[1]
    month_end   = today.replace(day=last_day)

    existing = db.query(Payment).filter(
        Payment.client_id    == client.id,
        Payment.payment_type == PaymentType.muhasebe,
        Payment.due_date     >= month_start,
        Payment.due_date     <= month_end,
    ).first()
    if existing:
        return  # Bu ay için zaten kayıt var

    due_day  = min(client.fee_due_day or 5, last_day)
    due_date = today.replace(day=due_day)

    p = Payment(
        client_id    = client.id,
        title        = f"Muhasebe Ücreti — {today.strftime('%B %Y')}",
        payment_type = PaymentType.muhasebe,
        amount       = float(client.monthly_fee),
        due_date     = due_date,
        status       = PaymentStatus.pending,
        recurrence   = "monthly",
        is_personal  = False,
    )
    db.add(p)
    db.commit()


@router.get("/", response_model=List[ClientOut])
def list_clients(active_only: bool = False, db: Session = Depends(get_db), _=Depends(require_auth)):
    q = db.query(Client)
    if active_only:
        q = q.filter(Client.is_active == True)
    return q.order_by(Client.full_name).all()

@router.post("/", response_model=ClientOut)
def create_client(data: ClientCreate, db: Session = Depends(get_db), _=Depends(require_auth)):
    client = Client(**data.model_dump())
    db.add(client)
    db.commit()
    db.refresh(client)
    _ensure_monthly_fee(client, db)   # ← bu aya ait muhasebe ücreti kaydını oluştur
    return client

@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    return c

@router.put("/{client_id}", response_model=ClientOut)
def update_client(client_id: str, data: ClientUpdate, db: Session = Depends(get_db), _=Depends(require_auth)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    _ensure_monthly_fee(c, db)   # ← aylık ücret değiştiyse bu ayı güncelle
    return c

@router.delete("/{client_id}")
def delete_client(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    c.is_active = False
    db.commit()
    return {"ok": True}


# ── Bakiye işlemleri ──────────────────────────────────────
@router.post("/{client_id}/add-balance")
def add_balance(client_id: str, body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    """Müşterinin bakiyesine tutar ekle (ödenmemiş muhasebe ücreti birikimi)"""
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    amount = float(body.get("amount", 0))
    c.balance = float(c.balance or 0) + amount
    db.commit()
    return {"ok": True, "balance": float(c.balance)}

@router.post("/{client_id}/clear-balance")
def clear_balance(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    """Bakiyeyi sıfırla (müşteri ödedi)"""
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    old_balance = float(c.balance or 0)
    c.balance = 0
    db.commit()

    # Tahsilat hareketi kaydet
    if old_balance > 0:
        tx = ClientTransaction(
            client_id=c.id,
            tx_type="credit",
            amount=old_balance,
            description=f"Bakiye tahsil edildi",
            tx_date=date.today(),
        )
        db.add(tx)
        db.commit()

    return {"ok": True}

@router.post("/{client_id}/carry-balance")
def carry_balance(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    """Muhasebe ücreti ödenmedi — bakiyeye ekle ve yeni aya taşı"""
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    if not c.monthly_fee:
        raise HTTPException(400, "Bu müşterinin aylık ücreti tanımlı değil")

    today = date.today()

    # ── Çift tıklama koruması: aynı ay içinde zaten devir yapıldı mı? ──
    month_start = today.replace(day=1)
    month_end_day = calendar.monthrange(today.year, today.month)[1]
    month_end = today.replace(day=month_end_day)

    already_carried = db.query(ClientTransaction).filter(
        ClientTransaction.client_id == c.id,
        ClientTransaction.tx_type == "debit",
        ClientTransaction.tx_date >= month_start,
        ClientTransaction.tx_date <= month_end,
        ClientTransaction.description.like(f"%{today.strftime('%m/%Y')}%"),
    ).first()

    if already_carried:
        raise HTTPException(400, f"Bu müşteri için {today.strftime('%m/%Y')} ayında zaten devir işlemi yapılmış! (Mükerrer işlem engellendi)")

    fee = float(c.monthly_fee)
    c.balance = float(c.balance or 0) + fee
    db.commit()

    # Borç hareketi kaydet
    tx = ClientTransaction(
        client_id=c.id,
        tx_type="debit",
        amount=fee,
        description=f"Muhasebe ücreti tahakkuk ({today.strftime('%m/%Y')})",
        tx_date=today,
    )
    db.add(tx)

    # Yeni aya devir ödeme kaydı oluştur
    if today.month == 12:
        next_month = today.replace(year=today.year+1, month=1, day=c.fee_due_day or 5)
    else:
        try:
            next_month = today.replace(month=today.month+1, day=c.fee_due_day or 5)
        except ValueError:
            next_month = today.replace(month=today.month+1, day=28)

    p = Payment(
        client_id=c.id,
        title=f"Muhasebe Ücreti Devir ({today.strftime('%m/%Y')}) — Bakiye: {float(c.balance):.2f} ₺",
        payment_type=PaymentType.muhasebe,
        amount=float(c.balance),
        due_date=next_month,
        status=PaymentStatus.pending,
    )
    db.add(p)
    db.commit()
    return {"ok": True, "balance": float(c.balance), "next_due": str(next_month)}


# ── Müşteri Hareketleri ───────────────────────────────────
@router.get("/{client_id}/transactions")
def get_transactions(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    txs = db.query(ClientTransaction).filter(
        ClientTransaction.client_id == client_id
    ).order_by(ClientTransaction.tx_date.desc(), ClientTransaction.created_at.desc()).all()
    return [{"id": t.id, "tx_type": t.tx_type, "amount": float(t.amount),
             "description": t.description, "tx_date": str(t.tx_date),
             "created_at": t.created_at.isoformat()} for t in txs]

@router.post("/{client_id}/transactions")
def add_transaction(client_id: str, body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "Müşteri bulunamadı")
    tx = ClientTransaction(
        client_id=client_id,
        tx_type=body.get("tx_type", "debit"),
        amount=float(body.get("amount", 0)),
        description=body.get("description", ""),
        tx_date=date.fromisoformat(body.get("tx_date", str(date.today()))),
    )
    db.add(tx)
    # Bakiyeyi güncelle
    if tx.tx_type == "debit":
        c.balance = float(c.balance or 0) + float(tx.amount)
    else:
        c.balance = max(0, float(c.balance or 0) - float(tx.amount))
    db.commit()
    return {"ok": True, "balance": float(c.balance)}


# ── Müşteri Evrakları ─────────────────────────────────────
@router.get("/{client_id}/documents")
def get_documents(client_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    docs = db.query(ClientDocument).filter(
        ClientDocument.client_id == client_id
    ).order_by(ClientDocument.created_at.desc()).all()
    return [{"id": d.id, "doc_name": d.doc_name, "file_path": d.file_path,
             "expiry_date": str(d.expiry_date) if d.expiry_date else None,
             "notes": d.notes, "created_at": d.created_at.isoformat()} for d in docs]

@router.post("/{client_id}/documents")
def add_document(client_id: str, body: dict, db: Session = Depends(get_db), _=Depends(require_auth)):
    doc = ClientDocument(
        client_id=client_id,
        doc_name=body.get("doc_name", ""),
        file_path=body.get("file_path"),
        expiry_date=date.fromisoformat(body["expiry_date"]) if body.get("expiry_date") else None,
        notes=body.get("notes"),
    )
    db.add(doc)
    db.commit()
    return {"ok": True, "id": doc.id}

@router.delete("/{client_id}/documents/{doc_id}")
def delete_document(client_id: str, doc_id: str, db: Session = Depends(get_db), _=Depends(require_auth)):
    doc = db.query(ClientDocument).filter(
        ClientDocument.id == doc_id, ClientDocument.client_id == client_id
    ).first()
    if not doc:
        raise HTTPException(404, "Evrak bulunamadı")
    db.delete(doc)
    db.commit()
    return {"ok": True}

