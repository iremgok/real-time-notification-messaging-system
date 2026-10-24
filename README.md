# Gerçek Zamanlı Bildirim & Mesajlaşma Sistemi

FastAPI + WebSocket + Redis Pub/Sub + PostgreSQL/SQLite. Birden fazla worker/sunucu ile yatay ölçeklenir.

## Hızlı başlangıç (Docker)

```bash
cp .env.example .env        # isteğe bağlı; yoksa geliştirme varsayılanları kullanılır
docker compose up --build   # http://localhost:8000  (demo arayüz + /docs)
docker compose up --build --scale app=3   # 3 replika, nginx önünde
```

Compose; PostgreSQL, Redis, uygulama (varsayılan 2 uvicorn worker) ve nginx'i (WebSocket proxy) ayağa kaldırır.
Tablolar konteyner açılışında `python -m src.manage init-db` ile **bir kez** oluşturulur.

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
src/
├── main.py                 # uygulama fabrikası + lifespan (Redis, tablolar, Pub/Sub listener)
├── manage.py               # init-db, promote-admin
├── core/                   # config (pydantic-settings), logger (gizli veri maskeleme)
├── models/                 # database (engine/session), entities (ORM), schemas (REST), ws_schemas (WS protokolü)
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

## Bilinen sınırlar / sonraki adımlar

- Şema **değişiklikleri** için migration aracı (Alembic) henüz yok; `create_all` yalnızca eksik tabloları oluşturur.
  Eski sürümden gelen bir Postgres volume'u varsa yeni kolonlar (`is_admin`, `dm_key` …) için `docker compose down -v` gerekir.
- Grup odalarında teslim durumu mesaj başına tektir (alıcı başına değil).
- Grup odaları ID'yi bilen herkesin katılımına açıktır (davet/rol sistemi yok).
- Her instance tüm `chat:*` kanallarını dinler; çok büyük kümelerde oda bazlı dinamik abonelik veya Redis Streams düşünülmeli.
