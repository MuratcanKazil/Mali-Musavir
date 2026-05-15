# Mali Müşavir Paneli v4 — Masaüstü

## Ne Değişti? (v3 → v4)
- PostgreSQL → SQLite (kurulum gerektirmez, tek dosya)
- SMS → WhatsApp link sistemi (ücretsiz, API yok)
- Bulut deploy → yerel çalışır (internet gerekmez)
- Veriler data/mali_musavir.db dosyasında saklanır

---

## Kurulum (İlk Kez)

1. Python kur: https://www.python.org/downloads/ (3.10 veya üstü)

2. CMD aç, backend klasörüne gir:
   cd mali-musavir-desktop\backend
   pip install -r requirements.txt

---

## Çalıştırma

Yöntem A: baslat.bat dosyasına çift tıkla (en kolay)

Yöntem B - CMD ile:
   cd mali-musavir-desktop\backend
   python -m uvicorn main:app --host 127.0.0.1 --port 8000
   
Sonra frontend/index.html dosyasını tarayıcıda aç.

---

## Giriş Bilgileri
Kullanıcı adı: admin
Şifre: Mali2024!

---

## WhatsApp Nasıl Çalışır?

1. Müşteri listesinden WA butonuna tıkla
2. Taslak seç, tutar ve tarihi gir
3. "WhatsApp Linki Oluştur" butonuna bas
4. Mesajı gözden geçir
5. "WhatsApp Web'de Aç & Gönder" butonuna bas
6. Tarayıcıda WhatsApp Web açılır, mesaj hazır gelir
7. Sadece Gönder tuşuna bas

WhatsApp Web'e ilk girişte telefon ile QR kod taratman yeterli.
Sonrasında oturum açık kalır.

---

## Yedekleme
data/mali_musavir.db dosyasını kopyalamak yeterli.
Tüm veriler bu tek dosyada.
