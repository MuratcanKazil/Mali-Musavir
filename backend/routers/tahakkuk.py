from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional
import io, re, logging
from datetime import date, datetime
from database import get_db
from models import Client, Payment, PaymentType, PaymentStatus

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Tür kodu → PaymentType eşleştirmesi ──────────────────
TUR_KODU_MAP = {
    # Stopaj / Gelir Vergisi Stopajı
    "0003": PaymentType.stopaj,
    "STPJ": PaymentType.stopaj,
    # KDV
    "0015": PaymentType.kdv,
    "KDVA": PaymentType.kdv,
    "KDV":  PaymentType.kdv,
    # Geçici Vergi
    "0032": PaymentType.gecici_vergi,
    "GGV":  PaymentType.gecici_vergi,
    "GV":   PaymentType.gecici_vergi,
    # Gelir Vergisi
    "0001": PaymentType.gelir_vergisi,
    "GVS":  PaymentType.gelir_vergisi,
    # Damga Vergisi
    "1047": PaymentType.damga_vergisi,
    "DVER": PaymentType.damga_vergisi,
    "DV":   PaymentType.damga_vergisi,
    # Özel İletişim Vergisi
    "1048": PaymentType.ozel_iletisim,
    "5035": PaymentType.ozel_iletisim,
    "OİV":  PaymentType.ozel_iletisim,
    # ÖTV
    "9077": PaymentType.otv,
    "ÖTV":  PaymentType.otv,
    "ÖTV2": PaymentType.otv,
    # Motorlu Taşıt
    "MTV":  PaymentType.motorlu_tasit,
}

def parse_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return None

def parse_date(s: str) -> Optional[date]:
    """DD/MM/YYYY → date"""
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except:
            pass
    return None

def extract_text_from_pdf(file_bytes: bytes) -> str:
    """pdfplumber ile metin çek"""
    import pdfplumber
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t:
                text += t + "\n"
    return text

def extract_text_from_image(file_bytes: bytes) -> str:
    """Görsel PDF için pytesseract ile OCR"""
    import pytesseract
    from PIL import Image
    img = Image.open(io.BytesIO(file_bytes))
    # Türkçe + İngilizce karışık dene
    try:
        return pytesseract.image_to_string(img, lang="tur")
    except:
        return pytesseract.image_to_string(img, lang="eng")

def parse_tahakkuk(text: str) -> dict:
    """
    Tahakkuk fişi metnini parse eder.
    Döndürür: {vergi_no, ad, vadesi, satirlar: [{tur_kodu, tur_adi, odenecek}]}
    """
    result = {
        "vergi_no": None,
        "ad": None,
        "vadesi": None,
        "satirlar": [],
        "raw_text": text,
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # ── Vergi Kimlik Numarası ─────────────────────────────
    for i, line in enumerate(lines):
        if "VERGİ KİMLİK" in line.upper() or "VERGİ NO" in line.upper():
            # Aynı satırda ya da sonraki satırda numara olabilir
            nums = re.findall(r"\b\d{10,11}\b", line)
            if not nums and i + 1 < len(lines):
                nums = re.findall(r"\b\d{10,11}\b", lines[i+1])
            if nums:
                result["vergi_no"] = nums[0]
            # T.C. Kimlik No da olabilir (11 hane)
            tc = re.findall(r":\s*(\d{10,11})", line)
            if tc:
                result["vergi_no"] = tc[0]

    # Satır içi "VERGİ KİMLİK NUMARASI : 1234567890" formatı
    m = re.search(r"VERGİ\s+KİMLİK\s+NUMARASI\s*:?\s*(\d{10,11})", text, re.IGNORECASE)
    if m:
        result["vergi_no"] = m.group(1)

    # ── Ad / Ünvan ────────────────────────────────────────
    for i, line in enumerate(lines):
        if "SOYADI" in line.upper() and "ÜNVAN" in line.upper():
            # Sonraki satır veya aynı satırın devamı
            if i + 1 < len(lines):
                ad_line = lines[i + 1]
                if not any(x in ad_line.upper() for x in ["ADI", "VERGİ", "ADRES"]):
                    result["ad"] = ad_line
        if line.upper().startswith("ADI") and not result.get("ad"):
            val = re.sub(r"^ADI\s*:?\s*", "", line, flags=re.IGNORECASE).strip()
            if val:
                result["ad"] = val

    # ── Vade tarihi ───────────────────────────────────────
    # Tablodaki tarihlerden son olanı al (vadesi)
    dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", text)
    if dates:
        parsed = [parse_date(d) for d in dates]
        parsed = [d for d in parsed if d]
        if parsed:
            result["vadesi"] = max(parsed)

    # ── Tablo satırları ───────────────────────────────────
    # Format: TÜNO TÜR_ADI MATRAH ORAN TAHAKKUK MAHSUP ÖDENECEK VADESİ
    # Örnek: "0032 GGV 11.190,08 1.678,51 1.199,99 478,52 17/11/2019"
    # veya:  "1047 DVER 0,00 26,90 0,00 26,90 17/11/2019"
    
    tablo_pattern = re.compile(
        r"(\d{4})\s+([A-ZÇŞĞÜÖİ0-9]{2,6})\s+"  # kod + kısa ad
        r"([\d.,]+)\s+"                            # matrah
        r"(?:[\d.,]+\s+)?"                         # oran (opsiyonel)
        r"([\d.,]+)\s+"                            # tahakkuk
        r"([\d.,]+)\s+"                            # mahsup
        r"([\d.,]+)\s+"                            # ödenecek
        r"(\d{2}/\d{2}/\d{4})",                   # vade
        re.MULTILINE
    )

    for m in tablo_pattern.finditer(text):
        kod      = m.group(1)
        tur_adi  = m.group(2)
        odenecek = parse_float(m.group(6))
        vade     = parse_date(m.group(7))

        pt = TUR_KODU_MAP.get(kod) or TUR_KODU_MAP.get(tur_adi)

        result["satirlar"].append({
            "tur_kodu":    kod,
            "tur_adi":     tur_adi,
            "payment_type": pt.value if pt else None,
            "odenecek":    odenecek,
            "vadesi":      vade.isoformat() if vade else None,
        })

    # Fallback: sadece ödenecek + vade olan basit satırlar
    if not result["satirlar"]:
        simple = re.compile(
            r"(\d{4})\s+(\S+)\s+[\d.,]+\s+([\d.,]+)\s+(\d{2}/\d{2}/\d{4})"
        )
        for m in simple.finditer(text):
            kod     = m.group(1)
            tur_adi = m.group(2)
            odenecek= parse_float(m.group(3))
            vade    = parse_date(m.group(4))
            pt      = TUR_KODU_MAP.get(kod) or TUR_KODU_MAP.get(tur_adi)
            result["satirlar"].append({
                "tur_kodu": kod, "tur_adi": tur_adi,
                "payment_type": pt.value if pt else None,
                "odenecek": odenecek,
                "vadesi": vade.isoformat() if vade else None,
            })

    return result


# ── Endpoint: PDF parse (önizleme) ───────────────────────
@router.post("/parse")
async def parse_file(file: UploadFile = File(...)):
    """PDF veya görsel yükle, tahakkuk fişini parse et, önizleme döndür."""
    data = await file.read()
    ct   = file.content_type or ""

    if "pdf" in ct or file.filename.lower().endswith(".pdf"):
        text = extract_text_from_pdf(data)
        if len(text.strip()) < 20:
            # Boş metin → taranmış PDF → OCR dene
            from pdf2image import convert_from_bytes
            import pytesseract
            images = convert_from_bytes(data, dpi=200)
            text = "\n".join(pytesseract.image_to_string(img, lang="eng") for img in images)
    else:
        # JPEG / PNG
        text = extract_text_from_image(data)

    parsed = parse_tahakkuk(text)
    return parsed


# ── Endpoint: Ödemeleri sisteme kaydet ───────────────────
@router.post("/apply")
async def apply_tahakkuk(body: dict, db: Session = Depends(get_db)):
    """
    body: {
      client_id: str,           # eşleşen müşteri
      satirlar: [{payment_type, odenecek, vadesi, tur_adi}]
    }
    """
    client_id = body.get("client_id")
    if client_id:
        c = db.query(Client).filter(Client.id == client_id).first()
        if not c:
            raise HTTPException(404, "Müşteri bulunamadı")

    created = []
    for s in body.get("satirlar", []):
        if not s.get("payment_type") or not s.get("odenecek"):
            continue
        try:
            pt = PaymentType(s["payment_type"])
        except:
            continue

        due = s.get("vadesi")
        if due:
            try:
                due = date.fromisoformat(due)
            except:
                due = date.today()
        else:
            due = date.today()

        p = Payment(
            client_id=client_id,
            title=f"{s.get('tur_adi','')} — Tahakkuk",
            payment_type=pt,
            amount=s["odenecek"],
            due_date=due,
            status=PaymentStatus.pending,
        )
        db.add(p)
        created.append(s)

    db.commit()
    return {"ok": True, "created": len(created)}
