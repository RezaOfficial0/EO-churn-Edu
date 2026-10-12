# Giriş ekranı ve Profil — sunum demosu

Dashboard'daki giriş ekranı, bot kontrol kutusu ve Profil sekmesi (frontend
`feature/login-page`) **sadece görünüm**. Bu belge neyin sahte olduğunu ve gerçek
sürümün nasıl çalışacağını özetliyor.

## Şu an ne yapıyor

| Parça | Demo davranışı |
|---|---|
| Giriş | E-posta ve şifre dolu, bot kutusu işaretliyse giriş yapılır. Hiçbir şey doğrulanmaz, backend'e istek gitmez. |
| Oturum | `{name, email}` tarayıcının `localStorage`'ına yazılır (`churn-demo-user`). Şifre hiçbir yerde saklanmaz. |
| Kullanıcı adı | E-postanın `@` öncesinden üretilir (`ayse.yilmaz` → `Ayse Yilmaz`), başlıkta tarihin sağında gösterilir. |
| Bot kontrolü | Tıklayınca yeşile döner. Token üretmez, doğrulama yapmaz. Altında "Test aşamasındadır" yazar. |
| Profil | Ad/e-posta değişikliği sadece `localStorage`'a yazılır. Şifre formu yalnızca "aynı mı, en az 8 karakter mi" kontrolü yapar, kaydetmez. |
| API | Değişmedi. Giriş ekranı API erişimini etkilemiyor. |

**Bu hâliyle canlıya çıkmamalı:** `localStorage`'a elle bir değer yazan herkes
"giriş yapmış" olur.

## Gerçek sürüm nasıl olur

### Veritabanı
```sql
CREATE TABLE users (
    id            BIGSERIAL   PRIMARY KEY,
    email         CITEXT      NOT NULL UNIQUE,
    display_name  TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,          -- argon2id, düz şifre asla
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
CREATE TABLE sessions (
    id         TEXT        PRIMARY KEY,          -- rastgele 32 bayt, cookie'deki değer
    user_id    BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
```
Kullanıcılar kendi kendine kayıt olmaz; ilk hesaplar bir script ile açılır
(B2B ürün, müşteri başına birkaç mentor).

### Uçlar
| Uç | İş |
|---|---|
| `POST /auth/login` | `{email, password, turnstile_token}`. Önce bot token'ı sağlayıcıya (ör. Cloudflare Turnstile `siteverify`) sunucudan doğrulatılır, sonra `argon2.verify`. Başarılıysa `sessions` satırı + `HttpOnly; Secure; SameSite=Lax` cookie. Hatalı girişte tek tip mesaj ("e-posta veya şifre hatalı"), IP/e-posta başına hız sınırı. |
| `POST /auth/logout` | Oturum satırını siler, cookie'yi temizler. |
| `GET /me` | `{display_name, email}` — başlıktaki kullanıcı adı buradan gelir, `localStorage`'dan değil. |
| `PATCH /me` | Ad / e-posta değişikliği. E-posta değişince doğrulama e-postası. |
| `POST /me/password` | `{current_password, new_password}`. Mevcut şifre doğrulanır, yeni hash yazılır, kullanıcının diğer oturumları silinir. |

Şifre her zaman HTTPS üzerinden istek **gövdesinde** gider; URL'de, log'da ya da
`localStorage`'da asla durmaz. `/auth/*` istek gövdeleri hiçbir log'a yazılmamalı.

### Mevcut API ile ilişkisi
Bugün skorlama uçlarını `X-API-Key` koruyor (`require_api_key`), ama frontend
(`src/api.js`) bu başlığı göndermiyor: dashboard yalnızca `API_KEY` tanımsız
açılan yerel geliştirme sunucusuyla konuşabiliyor. Gerçek girişle birlikte
tarayıcı oturum cookie'si ile gelir ve dashboard'un çağırdığı uçlarda
`require_api_key` yanında `require_session` da kabul edilir. API anahtarı
makineler (scheduler, entegrasyonlar) için kalır; tarayıcıya hiç verilmez.

### Frontend'de değişecek yerler
- `src/auth.js`: `localStorage` yerine `/auth/login`, `/me`, `/auth/logout` çağrıları.
- `BotCheck.jsx`: gerçek Turnstile widget'ı; ürettiği token login isteğine eklenir.
- `ProfileScreen.jsx`: formlar `PATCH /me` ve `POST /me/password`'e gider, hata mesajları backend'den gelir.
- `api.js`: `fetch(..., { credentials: 'include' })`, 401 gelince giriş ekranına dönüş.
