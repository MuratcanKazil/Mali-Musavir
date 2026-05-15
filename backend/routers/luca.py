"""
Luca Muhasebe Yazılımı — Excel Fiş Aktarım Modülü
Luca'nın "Muhasebe > Fiş İşlemleri > Excel Veri Aktarımı" özelliğiyle uyumlu format.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
import io, openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from database import get_db
from models import Payment, Client, PaymentType, PaymentStatus
from routers.auth import require_auth

router = APIRouter()

# Luca hesap kodu eşlemeleri (standart tek düzen hesap planı)
LUCA_ACCOUNT_MAP = {
    "kdv":                    {"borc": "191", "alacak": "360", "aciklama": "KDV Beyannamesi"},
    "stopaj":                 {"borc": "193", "alacak": "360", "aciklama": "Muhtasar Beyanname"},
    "muhasebe_ucreti":        {"borc": "770", "alacak": "120", "aciklama": "Muhasebe Ücreti"},
    "gelir_vergisi":          {"borc": "193", "alacak": "360", "aciklama": "Gelir Vergisi"},
    "gecici_vergi":           {"borc": "193", "alacak": "360", "aciklama": "Geçici Vergi"},
    "sgk":                    {"borc": "361", "alacak": "103", "aciklama": "SGK Primi"},
    "kisisel":                {"borc": "131", "alacak": "100", "aciklama": "Kişisel Ödeme"},
    "damga_vergisi":          {"borc": "193", "alacak": "360", "aciklama": "Damga Vergisi"},
    "ozel_iletisim_vergisi":  {"borc": "193", "alacak": "360", "aciklama": "Özel İletişim Vergisi"},
    "otv":                    {"borc": "193", "alacak": "360", "aciklama": "ÖTV"},
    "motorlu_tasit_vergisi":  {"borc": "257", "alacak": "360", "aciklama": "Motorlu Taşıtlar Vergisi"},
}

PT_LABELS = {
    "kdv": "KDV", "stopaj": "Stopaj", "muhasebe_ucreti": "Muhasebe Ücreti",
    "gelir_vergisi": "Gelir Vergisi", "gecici_vergi": "Geçici Vergi",
    "sgk": "SGK Primi", "kisisel": "Kişisel", "damga_vergisi": "Damga Vergisi",
    "ozel_iletisim_vergisi": "Özel İletişim Vergisi",
    "otv": "ÖTV", "motorlu_tasit_vergisi": "Motorlu Taşıt Vergisi",
}


@router.get("/export")
def export_luca_excel(
    date_from:    Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to:      Optional[str] = Query(None, description="YYYY-MM-DD"),
    client_id:    Optional[str] = Query(None),
    payment_type: Optional[str] = Query(None),
    status:       Optional[str] = Query(None, description="pending|paid|overdue"),
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """Seçilen ödemeleri Luca Excel fiş formatında indir."""
    q = db.query(Payment).filter(Payment.is_personal == False)

    if date_from:
        q = q.filter(Payment.due_date >= date.fromisoformat(date_from))
    if date_to:
        q = q.filter(Payment.due_date <= date.fromisoformat(date_to))
    if client_id:
        q = q.filter(Payment.client_id == client_id)
    if payment_type:
        q = q.filter(Payment.payment_type == payment_type)
    if status:
        q = q.filter(Payment.status == status)

    payments = q.order_by(Payment.due_date).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Luca Fiş Aktarımı"

    # ── Başlık satırı ────────────────────────────────────────
    header_fill   = PatternFill("solid", fgColor="18160F")
    header_font   = Font(bold=True, color="FFFFFF", size=10)
    header_border = Border(
        bottom=Side(style="thin", color="444444"),
    )
    headers = [
        "Fiş No", "Fiş Tarihi", "Fiş Türü", "Borç Hesabı", "Alacak Hesabı",
        "Tutar (₺)", "Açıklama", "Müşteri / Cari", "Vergi No", "Ödeme Durumu",
    ]
    col_widths = [10, 14, 18, 14, 14, 14, 40, 28, 14, 14]

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.fill   = header_fill
        cell.font   = header_font
        cell.border = header_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 22

    for ci, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(1, ci).column_letter].width = w

    # ── Veri satırları ───────────────────────────────────────
    alt_fill   = PatternFill("solid", fgColor="F7F4EF")
    paid_fill  = PatternFill("solid", fgColor="E8F3EC")
    overdue_fill = PatternFill("solid", fgColor="FDECEA")
    normal_font = Font(size=10)
    center = Alignment(horizontal="center")

    STATUS_TR = {"pending": "Bekliyor", "paid": "Ödendi", "overdue": "Gecikmiş"}

    for ri, p in enumerate(payments, 2):
        pt_key = p.payment_type.value if p.payment_type else "kisisel"
        acc    = LUCA_ACCOUNT_MAP.get(pt_key, {"borc": "???", "alacak": "???", "aciklama": ""})
        client = p.client
        status_str = STATUS_TR.get(p.status.value if p.status else "pending", "Bekliyor")

        # Satır rengi
        if p.status and p.status.value == "paid":
            row_fill = paid_fill
        elif p.status and p.status.value == "overdue":
            row_fill = overdue_fill
        elif ri % 2 == 0:
            row_fill = alt_fill
        else:
            row_fill = None

        row_vals = [
            f"F{ri-1:04d}",
            p.due_date.strftime("%d.%m.%Y") if p.due_date else "",
            PT_LABELS.get(pt_key, pt_key),
            acc["borc"],
            acc["alacak"],
            float(p.amount) if p.amount else 0.0,
            f"{acc['aciklama']} — {p.title}",
            client.full_name if client else "Genel",
            client.tax_number if client else "",
            status_str,
        ]

        for ci, val in enumerate(row_vals, 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = normal_font
            if row_fill:
                cell.fill = row_fill
            if ci in (1, 4, 5, 6, 9, 10):
                cell.alignment = center
            if ci == 6 and isinstance(val, float):
                cell.number_format = '#,##0.00'

    # ── Özet satırı ──────────────────────────────────────────
    last_row = len(payments) + 2
    ws.cell(last_row, 1, "TOPLAM").font = Font(bold=True, size=10)
    total_cell = ws.cell(last_row, 6)
    total_cell.value = sum(float(p.amount or 0) for p in payments)
    total_cell.number_format = '#,##0.00'
    total_cell.font = Font(bold=True, size=10)
    total_cell.fill = PatternFill("solid", fgColor="E8F3EC")

    # ── Bilgi sekmesi ─────────────────────────────────────────
    ws2 = wb.create_sheet("Luca Aktarım Bilgisi")
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 50
    info_rows = [
        ("Uygulama", "Mali Müşavir Asistan Paneli"),
        ("Oluşturma Tarihi", date.today().strftime("%d.%m.%Y")),
        ("Toplam Kayıt", str(len(payments))),
        ("", ""),
        ("LUCA'YA AKTARMA ADIMLARI", ""),
        ("1.", "Bu Excel dosyasını kaydedin"),
        ("2.", "Luca MMP'yi açın"),
        ("3.", "Muhasebe > Fiş İşlemleri menüsüne gidin"),
        ("4.", "'Excel Veri Aktarımı' seçeneğine tıklayın"),
        ("5.", "Bu dosyayı seçin ve aktarım yapın"),
        ("", ""),
        ("NOT", "Hesap kodlarını kendi hesap planınıza göre düzenleyin."),
        ("NOT", "Borç/Alacak kolonları tek düzen hesap planı varsayılanıdır."),
    ]
    for r, (k, v) in enumerate(info_rows, 1):
        ws2.cell(r, 1, k).font = Font(bold=bool(k and not k[0].isdigit()), size=10)
        ws2.cell(r, 2, v).font = Font(size=10)

    # ── Çıktı ────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"luca_fis_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
