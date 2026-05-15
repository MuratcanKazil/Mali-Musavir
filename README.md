# 💼 Mali Müşavir Asistanı

Muhasebe ofisleri için geliştirilmiş, müşteri takibi ve vergi/prim hatırlatmalarını WhatsApp üzerinden otomatikleştiren masaüstü web uygulaması.

> Gerçek bir mali müşavirlik ofisinin ihtiyaçlarından doğdu — aktif olarak kullanımda.

---

## 🎯 Ne İşe Yarıyor?

Muhasebeciler onlarca müşterisinin KDV, SGK, stopaj ve muhasebe ücret ödemelerini takip etmek zorunda. Bu uygulama:

- Yaklaşan son ödeme tarihlerini otomatik tespit eder
- Müşteriye özel WhatsApp mesajı hazırlar
- Tek tıkla WhatsApp Web üzerinden gönderim sağlar
- Luca entegrasyonu ile muhasebe verilerini senkronize eder

---

## ✨ Özellikler

| Özellik | Açıklama |
|---|---|
| 👥 Müşteri Yönetimi | Müşteri bilgileri, vergi numaraları, iletişim kayıtları |
| 📅 Vergi Takvimi | KDV, SGK, stopaj son ödeme tarihlerini otomatik hesaplar |
| 💬 WhatsApp Entegrasyonu | Hazır mesaj taslakları ile tek tıkla gönderim |
| 🔔 Otomatik Hatırlatmalar | Scheduler ile planlı bildirimler |
| 📊 Dashboard | Aylık tahsilat durumu ve özet istatistikler |
| 🔗 Luca Entegrasyonu | Muhasebe yazılımı senkronizasyonu |
| 🔒 Güvenli Giriş | Oturum tabanlı kimlik doğrulama |

---

## 🛠️ Teknolojiler

**Backend**
- Python 3.10+
- FastAPI — REST API
- SQLAlchemy — ORM
- SQLite — veritabanı (kurulum gerektirmez)
- APScheduler — zamanlanmış görevler

**Frontend**
- Vanilla HTML / CSS / JavaScript
- Tek sayfa uygulama (SPA)

---

## 🚀 Kurulum

```bash
# 1. Repoyu klonla
git clone https://github.com/MuratcanKazil/Mali-Musavir.git
cd Mali-Musavir

# 2. Bağımlılıkları yükle
cd backend
pip install -r requirements.txt

# 3. Uygulamayı başlat
# Windows: ana klasördeki baslat.bat dosyasına çift tıkla
# Manuel:
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Tarayıcıda `http://localhost:8000` adresine git.

---

## 📁 Proje Yapısı

```
mali-musavir-v6/
├── backend/
│   ├── main.py              # Uygulama giriş noktası
│   ├── models.py            # Veritabanı modelleri
│   ├── schemas.py           # Pydantic şemaları
│   ├── database.py          # DB bağlantısı
│   ├── scheduler.py         # Otomatik hatırlatmalar
│   └── routers/
│       ├── clients.py       # Müşteri CRUD
│       ├── payments.py      # Ödeme takibi
│       ├── whatsapp.py      # WA entegrasyonu
│       ├── tax_calendar.py  # Vergi takvimi
│       ├── dashboard.py     # İstatistikler
│       ├── luca.py          # Luca senkronizasyonu
│       └── auth.py          # Kimlik doğrulama
├── frontend/
│   └── index.html           # Tek sayfa uygulama
└── baslat.bat               # Windows başlatıcı
```

---

## 💡 Geliştirme Süreci

Bu proje, gerçek bir iş problemi çözmek için sıfırdan geliştirildi. v1'den v6'ya iteratif geliştirme sürecinde:

- PostgreSQL → SQLite (dağıtım kolaylığı)
- SMS API → WhatsApp Web (ücretsiz, sıfır konfigürasyon)
- Bulut deploy → yerel çalışır masaüstü uygulama

---

## 📄 Lisans

MIT
