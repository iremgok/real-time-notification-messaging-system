# Gerçek Zamanlı Bildirim & Mesajlaşma Sistemi

FastAPI + WebSocket + Redis Pub/Sub + PostgreSQL/SQLite. Birden fazla worker/sunucu ile yatay ölçeklenir.

## Hızlı başlangıç (Docker)

```bash
cp .env.example .env        # isteğe bağlı; yoksa geliştirme varsayılanları kullanılır
docker compose up --build   # http://localhost:8000  (demo arayüz + /docs)
docker compose up --build --scale app=3   # 3 replika, nginx önünde
```

Compose; PostgreSQL, Redis, uygulama (varsayılan 2 uvicorn worker) ve nginx'i (WebSocket proxy) ayağa kaldırır.
Şema, konteyner açılışında `python -m src.manage migrate` (= `alembic upgrade head`) ile güncellenir; bkz. [Veritabanı migration'ları](#veritabanı-migrationları-alembic).

Admin yetkisi vermek için:

```bash
docker compose exec app python -m src.manage promote-admin <kullanici_adi>
```

## Yerel geliştirme (Docker'sız)

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
# Redis gerekli: docker run -p 6379:6379 redis:7-alpine
uvicorn src.main:app --reload --ws-max-size 16384
pytest                                               # Redis/Postgres gerektirmez (fakeredis + SQLite)
```

## Mimari

```
alembic.ini                 # Alembic ayarları (veritabanı URL'si burada YOK)
alembic/
├── env.py                  # async engine, Base.metadata, DATABASE_URL config.py'den
└── versions/               # migration dosyaları (20261024_0001_initial_schema.py ...)
src/
├── main.py                 # uygulama fabrikası + lifespan (Redis, tablolar, Pub/Sub listener)
├── manage.py               # migrate, promote-admin
├── core/                   # config (pydantic-settings), logger (gizli veri maskeleme)
├── models/                 # database (engine/session), entities (ORM), migrations (Alembic runner),
│                           # schemas (REST), ws_schemas (WS protokolü)
├── services/               # iş mantığı — HTTP/WS'den bağımsız
│   ├── auth_service.py     # bcrypt, JWT (PyJWT), kullanıcı işlemleri
│   ├── room_service.py     # grup / 1:1 oda, üyelik
│   ├── chat_service.py     # mesaj kaydı, teslim durumu, keyset sayfalama
│   ├── notification_service.py  # olayları Redis'e yayınlar
│   ├── redis_service.py    # Redis istemcisi + kendini onaran Pub/Sub dinleyicisi
│   ├── rate_limiter.py     # Redis tabanlı dağıtık hız sınırlama
│   └── presence_service.py # küme genelinde çevrimiçi durumu
├── websockets/
│   ├── endpoints.py        # /ws ve /ws/{room_id} + JWT el sıkışma
│   ├── manager.py          # yerel bağlantı havuzu, oda indeksi, fan-out
│   ├── connection.py       # soket başına sınırlı gönderim kuyruğu (yavaş istemci koruması)
│   └── handlers.py         # gelen çerçeveler: boyut/hız/doğrulama → servisler
├── api/                    # routes.py (+ /health), dependencies, errors, endpoints/*
└── static/                 # demo istemci
```

**Mesaj akışı:** istemci → `handlers` → `chat_service` (DB'ye kaydet) → `notification_service` → Redis `chat:room:<id>` →
**tüm** instance'ların Pub/Sub dinleyicisi → her instance'ın `ConnectionManager`'ı yalnızca kendi soketlerine iletir.

## API

| Yöntem | Yol | Açıklama |
|---|---|---|
| POST | `/api/v1/auth/register` | Kayıt (IP başına hız sınırlı) |
| POST | `/api/v1/auth/login` | OAuth2 form → `access_token` (IP başına hız sınırlı) |
| GET | `/api/v1/auth/me` | Oturumdaki kullanıcı |
| GET/POST | `/api/v1/rooms` | Odalarım / grup odası oluştur |
| POST | `/api/v1/rooms/direct` | 1:1 oda getir/oluştur (`{"target_username": ...}`) |
| POST | `/api/v1/rooms/{id}/join` · `/leave` | Grup odasına katıl / ayrıl |
| GET | `/api/v1/messages/{room_id}?limit=&cursor=` | Mesaj geçmişi (yalnız üyeler) |
| POST | `/api/v1/admin/broadcast` | Herkese bildirim (admin) |
| POST | `/api/v1/admin/notify/{user_id}` | Tek kullanıcıya bildirim (admin) |
| GET | `/api/v1/health` | DB + Redis kontrolü (sorun varsa 503) |

## WebSocket protokolü

- `/ws` — kullanıcının tüm odaları + bildirimler · `/ws/{room_id}` — tek odaya bağlı oturum (+ bildirimler)
- Token: `Sec-WebSocket-Protocol: access_token, <jwt>` (tarayıcı, önerilen) · `Authorization: Bearer <jwt>` · `?token=<jwt>`
- Geçersiz/süresi dolmuş token, üye olunmayan oda, izin verilmeyen Origin → **1008**. Token oturum sırasında dolarsa da 1008.

```jsonc
// istemci → sunucu
{"type": "ping"}
{"type": "send_message", "data": {"room_id": "<uuid>", "content": "merhaba", "client_id": "opsiyonel"}}
{"type": "message_ack",  "data": {"message_id": "<uuid>", "status": "DELIVERED" | "READ"}}
// sunucu → istemci: connected, pong, message, message_sent, message_status_update,
//                   presence_change, joined_room, left_room, notification, error
```

## Güvenlik ve operasyon

- **Limitler:** çerçeve boyutu (`WS_MAX_MESSAGE_BYTES`; uvicorn `--ws-max-size` + uygulama kontrolü → 1009), mesaj uzunluğu
  (`MESSAGE_MAX_LENGTH`), kullanıcı başına WS hız sınırı, IP başına login/register sınırı, kullanıcı başına bağlantı sayısı,
  soket başına gönderim kuyruğu (taşan yavaş istemci 1013 ile kapatılır).
- **Loglar:** JWT, `?token=`, `Bearer`, parola ve e-posta değerleri tüm log handler'larında (uvicorn dahil) maskelenir;
  nginx URL'yi sorgu dizesi olmadan loglar; SQLAlchemy parametreleri gizlenir. Kullanıcılar loglarda yalnızca ID ile geçer.
- **Kimlik doğrulama:** bcrypt (event loop dışında), zamanlama saldırısına karşı sahte doğrulama, `exp`/`iat`/`sub`/`type` zorunlu JWT.
- **`ENVIRONMENT=production`:** zayıf/varsayılan `SECRET_KEY` ve `*` CORS ile uygulama açılmaz; `/docs` kapanır.
- **Redis kesintisi:** Pub/Sub dinleyicisi üstel geri çekilmeyle yeniden bağlanır; hız sınırlayıcı "fail-open" çalışır (loglanır).
- **Teslim garantisi:** Pub/Sub "en fazla bir kez" iletir; mesajlar önce DB'ye yazıldığı için yeniden bağlanan istemci
  kaçırdıklarını `/messages` ile tamamlar.

## Veritabanı migration'ları (Alembic)

Şema yalnızca Alembic ile yönetilir (`create_all` kullanılmaz). `alembic/env.py`, `DATABASE_URL`'yi
`src/core/config.py` üzerinden (ortam değişkeni / `.env`) okur ve modelleri `Base.metadata`'dan alır.

**Şema ne zaman güncellenir?**

| Ortam | Mekanizma |
|---|---|
| Docker | Konteyner açılışında, worker'lar başlamadan önce `python -m src.manage migrate`. PostgreSQL advisory lock sayesinde `--scale app=N` ile aynı anda açılan replikalardan yalnızca biri migration uygular. |
| Yerel (`uvicorn`) | `DB_AUTO_MIGRATE=true` (varsayılan) ise uygulama açılırken `upgrade head` çalışır. Çok worker'lı kurulumlarda `false` yapıp migration'ı ayrı bir adımda çalıştırın. |

**Günlük kullanım** (proje kökünde; Docker içinde: `docker compose exec app alembic ...`):

```bash
# 1) src/models/entities.py içinde modeli değiştir
# 2) migration üret ve dosyayı MUTLAKA gözden geçir (yeniden adlandırmaları drop+add olarak algılayabilir)
alembic revision --autogenerate -m "add last_seen to users"
# 3) uygula / kontrol et
alembic upgrade head
alembic current            # veritabanının sürümü
alembic history            # tüm sürümler
alembic downgrade -1       # bir adım geri al
alembic upgrade head --sql # SQL'i çalıştırmadan yazdır (DBA incelemesi için)
```

**Kurallar**
- Modeli değiştirip migration üretmeyi unutursanız `tests/test_migrations.py` başarısız olur (modeller ile migration'lar karşılaştırılır).
- Kısıt isimleri `NAMING_CONVENTION` (`src/models/database.py`) ile sabittir; SQLite'ta `ALTER` kısıtları için batch modu açıktır.
- Yayına alınmış bir migration dosyasını değiştirmeyin; düzeltme için yeni bir revision ekleyin.
- Production'da `upgrade` öncesi yedek alın; yıkıcı adımları (kolon silme) iki sürüme yayın.

**Alembic'ten önce oluşturulmuş veritabanları:** `migrate`, tabloları olup `alembic_version` tablosu olmayan bir
veritabanı bulursa modellerle karşılaştırır. Tablo ve kolonlar eşleşiyorsa veritabanını `head` olarak işaretler
(`stamp`); eşleşmiyorsa hiçbir şeye dokunmadan hata verir (yedek alıp elle taşıyın veya veritabanını yeniden oluşturun).

**Güvenlik:** `alembic.ini` veritabanı URL'si içermez; migration'lar parametreleri gizleyen ayrı bir engine kullanır ve
loglardaki `scheme://kullanıcı:parola@` biçimindeki parolalar maskelenir.

## Bilinen sınırlar / sonraki adımlar

- İlk `app/` sürümünden kalan bir Postgres volume'u yeni kolonları (`is_admin`, `dm_key` …) içermediği için
  `migrate` tarafından reddedilir; yedek alıp `docker compose down -v` ile sıfırlayın.
- Grup odalarında teslim durumu mesaj başına tektir (alıcı başına değil).
- Grup odaları ID'yi bilen herkesin katılımına açıktır (davet/rol sistemi yok).
- Her instance tüm `chat:*` kanallarını dinler; çok büyük kümelerde oda bazlı dinamik abonelik veya Redis Streams düşünülmeli.
