#!/usr/bin/env bash
#
# EO-churn (model + backend) issue'larini olusturur
#
# Kullanim:
#   ./create_backend_issues.sh                 -> issue'lari olusturur
#   ./create_backend_issues.sh --dry-run       -> sadece ne yapacagini yazar, hicbir sey olusturmaz
#   ./create_backend_issues.sh owner/repo      -> baska bir repoyu hedefler (varsayilan: bulundugun repo)
#
# Gereken: gh (GitHub CLI) kurulu ve `gh auth status` basarili.
#
# Idempotent: ayni baslikta bir issue zaten varsa atlar. Yarim kalirsa tekrar
# calistirabilirsin, ayni issue iki kez olusmaz.
set -euo pipefail

DRY_RUN=0
REPO_ARG=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*)        echo "bilinmeyen parametre: $arg" >&2; exit 2 ;;
    *)         REPO_ARG=(--repo "$arg") ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "hata: gh bulunamadi -> https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "hata: gh giris yapilmamis -> gh auth login" >&2; exit 1; }

say() { printf '%s\n' "$*"; }
R=("${REPO_ARG[@]+"${REPO_ARG[@]}"}")

ensure_label() {
  local name="$1" color="$2" desc="$3"
  if [ "$DRY_RUN" = 1 ]; then say "  [dry] etiket: $name"; return; fi
  gh label create "$name" --color "$color" --description "$desc" "${R[@]+"${R[@]}"}" >/dev/null 2>&1 \
    || gh label edit "$name" --color "$color" --description "$desc" "${R[@]+"${R[@]}"}" >/dev/null 2>&1 \
    || true
  say "  etiket hazir: $name"
}

ensure_milestone() {
  local title="$1" slug
  if [ "$DRY_RUN" = 1 ]; then say "  [dry] milestone: $title"; return; fi
  slug="$(gh repo view "${R[@]+"${R[@]}"}" --json nameWithOwner -q .nameWithOwner)"
  if gh api "repos/$slug/milestones?state=all" -q '.[].title' 2>/dev/null | grep -Fxq "$title"; then
    say "  milestone var: $title"
  else
    gh api "repos/$slug/milestones" -f title="$title" >/dev/null && say "  milestone olusturuldu: $title"
  fi
}

CREATED=0
SKIPPED=0

mk() {
  local title="$1" labels="$2" milestone="$3" body url
  body="$(cat)"

  if gh issue list --state all --limit 400 --json title -q '.[].title' "${R[@]+"${R[@]}"}" 2>/dev/null \
       | grep -Fxq "$title"; then
    say "ATLA    $title"; SKIPPED=$((SKIPPED+1)); return
  fi

  if [ "$DRY_RUN" = 1 ]; then
    say "[dry]   $title"
    say "        $labels | $milestone"
    CREATED=$((CREATED+1)); return
  fi

  url="$(printf '%s' "$body" | gh issue create \
      --title "$title" --label "$labels" --milestone "$milestone" --body-file - \
      "${R[@]+"${R[@]}"}")"
  say "ACILDI  $title"
  say "        $url"
  CREATED=$((CREATED+1))
}

say "== Etiketler =="
ensure_label P0-demo     d73a4a "Demo oncesi kapanmali"
ensure_label P1-pilot    b60205 "Pilot oncesi pazarliksiz"
ensure_label P2          fbca04 "Borc"
ensure_label area:db     0e8a16 "Veritabani / sema"
ensure_label area:api    1d76db "FastAPI servisi"
ensure_label area:ml     5319e7 "Model / veri hatti"
ensure_label area:ops    006b75 "Docker, CI, isletim"
ensure_label area:notify c2e0c6 "Bildirimler"
ensure_label area:docs   bfd4f2 "Dokumantasyon"
ensure_label area:ui     0052cc "Arayuz"
ensure_label template    e99695 "Template refactoru"
ensure_label security    d93f0b "Guvenlik"
ensure_label privacy     d4c5f9 "KVKK / kisisel veri"
ensure_label product     c5def5 "Urun karari"

say ""
say "== Milestone'lar =="
ensure_milestone demo-ready
ensure_milestone pilot-ready
ensure_milestone backlog

say ""
say "== Issue'lar =="

mk "B-01 · Telegram bot token'ı hata mesajıyla ekrana ve log'a düşüyor" "P0-demo,area:notify,security" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:notify` `security`

**Sorun**
`src/notifications/channels.py:67` token'ı istek URL'sine gömüyor ve `_post_json`
sadece `HTTPError` yakalıyor. Token'da boşluk veya newline varsa (dotenv tırnak
içindeki boşluğu korur, k8s/Docker secret'ları sonda newline bırakabilir) urllib
`http.client.InvalidURL` fırlatıyor ve bu istisnanın mesajı **tam URL'yi, yani
token'ı** içeriyor. `notify.py:73-75` onu hem sonuç string'ine koyuyor hem
`logger.error`'a yazıyor, `send_daily_alerts.py:191` de stdout'a basıyor.

Doğrulanmış çıktı:
```
telegram  error: InvalidURL: URL can't contain control characters.
'/bot8123456789:AAFake...\n/sendMessage' (found at least '\n')
```

**Neden P0**
Canlı demoda müşterinin ekranında production bot token'ı görünür.
`scripts/telegram_setup.py:29-31` aynı şekle sahip ve orada hiç guard yok.

**Kabul kriterleri**
- [ ] Token yüklenirken `.strip()` ve boş/bozuk ise anlaşılır hata
- [ ] `_post_json` her istisnayı yakalıyor ve mesajdan token maskeleniyor
      (`bot***`)
- [ ] `telegram_setup.py` aynı korumayı kullanıyor
- [ ] Token içeren bir istisna mesajının maskelendiğini doğrulayan test

**Dosyalar:** `src/notifications/channels.py`, `src/notifications/notify.py`,
`scripts/telegram_setup.py`, `scripts/send_daily_alerts.py`
BODY

mk "B-02 · `demo_reset.sh` tek koşu bırakıyor, trend özelliği demoda hiç görünmüyor" "P0-demo,area:ops" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ops`

**Sorun**
`demo_reset.sh` `alerts`'i truncate edip **tek** koşu yapıyor.
`previous_run_probabilities_*` iki koşudan az varsa `{}` dönüyor
(`loader.py:350-351`), dolayısıyla her öğrenci `new` oluyor ve mesajdaki
`↑ (önceki %42)` okları ile tekrar bölümü **hiç oluşmuyor**. En çok emek verilen
özellik demoda garanti görünmez.

**Kabul kriterleri**
- [ ] `demo_reset.sh --with-history` (veya varsayılan olarak) iki koşu bırakıyor:
      biri dünün tarihiyle hafifçe farklı olasılıklarla, biri bugünün
- [ ] Sonuçta mesajda en az bir `↑`/`↓` oku ve en az bir tekrar satırı var
- [ ] README'nin "Before a demo" bölümü güncel

**Dosyalar:** `scripts/demo_reset.sh`, README
BODY

mk "B-03 · `daily_students` tarihli hale gelsin (`as_of_date`)" "P1-pilot,area:db,area:ml" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db` `area:ml`

**Sorun**
Tablo `student_id` primary key ile **tek fotoğraf** tutuyor ve her yükleme
öncekini eziyor (`loader.py:193-199` `ON CONFLICT DO UPDATE`). Sonuçları:

- trend özelliği üretilemiyor ("son 4 haftada devamsızlık arttı" gibi) — churn
  tahmininde en güçlü sinyaller neredeyse her zaman trend sinyalleridir, yani
  bu modelin zayıflığının **yapısal sebebi**
- dashboard'daki 14 günlük risk paneli, "listede üst üste" sayacı ve risk
  listesindeki delta kolonu veri kaynağı olmadığı için kapalı / "yok"
- müşteriye "verinizi biriktiriyoruz" denemiyor

**Kabul kriterleri**
- [ ] `daily_students` primary key `(entity_id, as_of_date)`
- [ ] Migration mevcut satırları bugünün tarihiyle taşıyor
- [ ] `load_daily_students.py` bir `--as-of` parametresi alıyor, varsayılan bugün
- [ ] Skorlama en son `as_of_date`'i kullanıyor
- [ ] `src/data/features.py`'de trend özelliği üretimi: `_delta_7d`, `_delta_28d`
      gibi, hangi kolonlar için olduğu config'den
- [ ] Yeterli geçmiş yoksa trend özellikleri null + `_missing` flag
- [ ] DB testleri: iki farklı `as_of_date` yükleme, en sonun skorlanması

**Dosyalar:** `db/schema.sql`, `db/migrations/00X_*.sql`, `src/data/loader.py`,
`src/data/features.py`, `scripts/load_daily_students.py`, `config.py`, `tests/`

> Bu issue template refactor'ünden **önce** kapanmalı; sonra kapanırsa her müşteri
> profilinde tekrar edilir.
BODY

mk "B-04 · `runs` tablosu: koşu kimliği ve boş koşu kaydı" "P1-pilot,area:db" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db`

**Sorun**
Bir koşunun kimliği Python tarafında üretilen mikrosaniyelik bir `run_at`
timestamp'i. Ayrıca `append_to_alert_log_db` riskli öğrenci yoksa **hiçbir şey
yazmıyor** (`loader.py:266-268`). Sonucu: "pipeline koştu, kimse riskli değil"
ile "pipeline 3 gündür ölü" ayırt edilemiyor. Bir alarm ürününde sessizlik
belirsiz olamaz.

Ayrıca `(run_at, student_id)` unique index'i iddia edilen çift-sayma korumasını
sağlamıyor: iki eşzamanlı koşu iki farklı mikrosaniye alır, ikisi de kabul edilir.

**Kabul kriterleri**
- [ ] `runs` tablosu: `run_id`, `started_at`, `finished_at`, `model_version`,
      `threshold`, `entity_count`, `at_risk_count`, `status`
      (`ok`/`no_alerts`/`failed`), `run_date` üzerinde unique
- [ ] `alerts.run_id` foreign key
- [ ] Riskli kimse yoksa `status='no_alerts'` satırı yazılıyor
- [ ] `previous_at_risk_ids` boş koşuyu "önceki koşu" olarak sayıyor (bugün
      atlıyor, bu yüzden bayat karşılaştırma yapıyor)
- [ ] `pg_advisory_xact_lock` ya da `run_date` unique ile eşzamanlı koşu engeli
- [ ] Test: üst üste iki koşu, boş koşu, eşzamanlı koşu

**Dosyalar:** `db/schema.sql`, migration, `src/data/loader.py`,
`pipeline/daily_pipeline.py`
BODY

mk "B-05 · `alerts`'e feature snapshot + `model_version` + `threshold`" "P1-pilot,area:db" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db`

**Sorun**
`score_students` skorladığı feature değerlerini **zaten hesaplıyor**
(`daily_pipeline.py:93` → `at_risk["features"]`), ama `append_to_alert_log_db`
insert listesinde yok — sessizce atılıyor. `alerts`'te model sürümü ve eşik de
yok.

Sonuçları: (a) "3 Eylül'de bu öğrenciyi neden işaretledin" sorusunun cevabı yok,
skor tekrar üretilemiyor; (b) eşik maliyet türevli olduğu için her retrain'de
kendiliğinden kayıyor, dolayısıyla alarm hacmi düşünce "churn mü iyileşti, eşik
mi kaydı" ayırt edilemiyor; (c) ürünün değer iddiasının dayandığı analiz
("işaretlediklerimizin kaçı gerçekten ayrıldı, ayrılanlar işaretlendiğinde nasıl
görünüyordu") yapılamıyor.

**Kabul kriterleri**
- [ ] `alerts.features JSONB NOT NULL`, `model_version TEXT NOT NULL`,
      `threshold DOUBLE PRECISION NOT NULL`
- [ ] `model_version`, `model_meta.json`'daki `data_sha256` + `trained_at`'ten
      türetiliyor
- [ ] `json.dumps(..., allow_nan=False)` + `to_native` bu yolda da uygulanıyor
      (şu an `top_reasons_detail` yazılırken `allow_nan` açık — bir NaN SHAP
      değeri tüm günün koşusunu geri alıyor)
- [ ] `CHECK (churn_probability >= 0 AND churn_probability <= 1)` — bugün NaN
      kabul ediliyor ve Postgres'te NaN en büyük değer olarak sıralanıyor, yani
      `ORDER BY ... DESC` bozuk satırı başa koyuyor

**Dosyalar:** `db/schema.sql`, migration, `src/data/loader.py`
BODY

mk "B-06 · Sonuç (label) ve müdahale kayıt tabloları" "P1-pilot,area:db,product" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db` `product`

**Sorun**
İki şey hiç kaydedilmiyor:

1. **Sonuç:** alarm verdiğimiz öğrenci gerçekten ayrıldı mı? Kaydedilmezse model
   ikinci ay hiç iyileşmez — veri çarkı buradan başlıyor.
2. **Müdahale:** mentor ne yaptı, ne zaman, sonuç ne oldu? Bu olmadan *"sistem
   sayesinde N öğrenci kaldı"* **hiçbir zaman** kanıtlanamaz, sadece korelasyon
   olur. Müşterinin yenileme kararı tam bu sayıya bakacak.

**Kabul kriterleri**
- [ ] `outcomes`: `entity_id`, `churned` (bool), `churned_at`, `source`,
      `recorded_at`
- [ ] `interventions`: `entity_id`, `run_id`, `actor`, `channel`, `acted_at`,
      `note`, `result`
- [ ] `scripts/load_outcomes.py` — müşterinin aylık "ayrılanlar" listesini alıyor
- [ ] `POST /interventions` (auth'lu) — dashboard'daki "iletişime geçildi"
      işaretini kalıcı hale getiriyor
- [ ] `scripts/report_outcomes.py` — işaretlenenlerin kaçı ayrıldı, uyarı süresi
      dağılımı, müdahale edilen vs edilmeyen karşılaştırması

**Dosyalar:** `db/schema.sql`, migration, `api/main.py`, `scripts/`
BODY

mk "B-07 · Kimlik doğrulama fail-closed olsun" "P1-pilot,area:api,security" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api` `security`

**Sorun**
`config.py:268` `API_KEY = os.environ.get("API_KEY") or None`, ve
`require_api_key` (`api/main.py:103-110`) `API_KEY is None` ise koşulsuz geçiyor.
Her iki env şablonu da boş ship ediyor, `docker-compose.yml:75` portu
`0.0.0.0`'a açıyor. Yani dokümante edilen kurulum yolu **kimlik doğrulaması
kapalı** bir servis üretiyor: `curl host:8000/students?threshold=0` ile tüm
öğrenci id'leri ve 24 feature değeri (çalışma saati, memnuniyet, ödeme gecikmesi)
kimlik olmadan dönüyor. Veri sahipleri çoğunlukla reşit olmayan öğrenciler.

`/docs`, `/redoc`, `/openapi.json` anahtar set edilse bile korumasız.
Karşılaştırma `!=` ile yapılıyor, sabit zamanlı değil.

**Kabul kriterleri**
- [ ] `API_KEY` yoksa ve bind adresi loopback değilse **başlatma reddediliyor**
- [ ] `EOAI_ALLOW_NO_AUTH=1` gibi açık bir opt-out sadece yerel geliştirme için
- [ ] `hmac.compare_digest`
- [ ] `/docs`, `/redoc`, `/openapi.json` anahtar arkasında
- [ ] `/health` auth'suz ama gövdesi `reason` (dosya yolu) **döndürmüyor**
- [ ] Test: anahtar set iken `/students`, `/metrics`, `POST /predict` **ve**
      `POST /run-daily-pipeline` 401 dönüyor; yanlış anahtar da 401
- [ ] CI'da `API_KEY` set edilerek koşuyor (bugün testi `skipif` ile atlıyor)

**Dosyalar:** `api/main.py`, `config.py`, `tests/test_api.py`,
`.github/workflows/ci.yml`
BODY

mk "B-08 · `POST /run-daily-pipeline`'ı HTTP yüzeyinden kaldır" "P1-pilot,area:api,security" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api` `security`

**Sorun**
Tek yazan endpoint; auth kapalı olduğu için CORS "simple request" sayılıyor,
yani tarayıcıdan bir form post'u preflight olmadan gidiyor ve yazma
gerçekleşiyor. CSRF token yok, kilit yok, idempotency yok, rate limit yok.

Her çağrı yeni bir "önceki koşu" yaratıyor → `_mark_new_or_repeat` tüm
öğrencileri `still_at_risk` işaretliyor → `send_daily_alerts` sadece `new`
olanları detaylandırdığı için sabah mesajı **boş "yeni öğrenciler" bölümüyle**
çıkıyor ve günün riskli öğrencileri hiç eskale edilmiyor. Saldırgan gerekmiyor;
bir dashboard butonuna çift tıklamak yeterli.

**Kabul kriterleri**
- [ ] Endpoint kaldırılıyor; günün koşusu zamanlayıcıya ait
      (`python -m pipeline.daily_pipeline`)
- [ ] Ya da: ayrı bir admin anahtarı + günde bir kez idempotency guard +
      advisory lock
- [ ] `API_CONTRACT.md` ve dashboard `src/api.js` güncel
- [ ] CSV yolundaki eşzamanlı `to_csv(mode="a")` yarışı da kapanıyor

**Dosyalar:** `api/main.py`, `API_CONTRACT.md`, `src/data/loader.py`
BODY

mk "B-09 · `GET /metrics` allow-list" "P1-pilot,area:api,security" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api` `security`

**Sorun**
`api/main.py:272` tüm `model_meta.json`'u döndürüyor. İçinde
`/Users/rizatalebi/...` mutlak yolları (`data_file`, `calibrator_path`),
`data_sha256`, `imputation_values` (eğitim medyanları), `model_params`,
`catboost_tree_count` ve `error_analysis_false_negatives` (sınıf/şehir/plan
kırılımında kaç churner kaçırıldığı) var. Sözleşmede 6-7 alan dokümante edilmiş.

**Kabul kriterleri**
- [ ] Açık allow-list: `is_synthetic_data`, `trained_at`, `chosen_threshold`,
      `calibration_method`, `data_rows`, `metrics`, `cv_auc_*`,
      `baseline_metrics`
- [ ] `model_meta.json`'daki yollar `BASE_DIR`'a göre **göreli** yazılıyor
- [ ] `API_CONTRACT.md` gerçek payload'ı yansıtıyor (kısmen yapıldı)
- [ ] Test: allow-list dışı bir alan eklenince response'a sızmıyor

**Dosyalar:** `api/main.py`, `pipeline/training_pipeline.py`, `API_CONTRACT.md`
BODY

mk "B-10 · API girdi sıkılaştırma" "P1-pilot,area:api" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api`

**Sorun** (dördü bir arada, hepsi aynı katman)

1. `threshold` sınırsız: `?threshold=nan` → 500 (Starlette `allow_nan=False` ile
   serialize ediyor). `?threshold=-inf` → önce **tüm veri üzerinde SHAP** koşuyor,
   sonra 500. `<0` ve `>1` sessizce kabul.
2. Kategorik alanlar hiç doğrulanmıyor: `(str, ...)`, enum yok, uzunluk sınırı
   yok, `""` kabul. `{"plan_type": "banana"}` 200 dönüyor ve CatBoost görülmemiş
   kategoriyi hash'leyip gerçekçi bir olasılık üretiyor.
3. Sayısal alanların hepsi `float`: `true` → `1.0`, sayaç alanına `2.7` kabul,
   `satisfaction_missing: 0.5` kabul.
4. `extra="ignore"` (pydantic varsayılanı): fazladan alan sessizce atılıyor —
   oysa `validate()` tam bu yüzden fazla kolonu reddediyor
   (`validation.py:45-52`).

**Kabul kriterleri**
- [ ] `threshold: float | None = Query(None, ge=0, le=1)`, `allow_inf_nan=False`
- [ ] Kategorikler `Literal[...]` (seviyeler profil/meta'dan) + `max_length`
- [ ] Flag ve sayaç alanları `int` / `bool`, `strict=True`
- [ ] `model_config = ConfigDict(extra="forbid")`
- [ ] `RequestValidationError` handler'ı 422 gövdesini `{"detail": "<string>"}`
      şekline indiriyor (sözleşme bunu vaat ediyor, FastAPI liste döndürüyor)
- [ ] Testler: her madde için bir vaka

**Dosyalar:** `api/main.py`, `API_CONTRACT.md`, `tests/test_api.py`
BODY

mk "B-11 · `to_native` tüm çıktı yollarında uygulanmalı" "P1-pilot,area:api" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api`

**Sorun**
`features` dict'i `to_native`'den geçiyor ama id kolonları geçmiyor
(`api/main.py:234`, `:259`, `:192` — `to_dict(orient="records")` ham). Günlük
veride **tek bir boş `enrollment_date`** varsa `nan` çıkıyor, Starlette
`allow_nan=False` ile serialize ettiği için `GET /students`,
`GET /predict/{id}` ve `POST /run-daily-pipeline` üçü birden
`{"detail": "internal server error"}` dönüyor. Doğrulama katmanı bunu bilerek
geçiriyor: `score_students` `max_null_ratio=1.0` veriyor ve `require_no_nulls`
sadece `FEATURES`'ı kapsıyor, `STUDENT_INFO`'yu değil.

**Kabul kriterleri**
- [ ] Tüm response gövdeleri tek bir `to_external()` fonksiyonundan geçiyor
- [ ] `STUDENT_INFO` kolonlarındaki null'lar `null` olarak serialize ediliyor
- [ ] Test: boş `enrollment_date` olan bir satırla üç endpoint de 200 dönüyor

**Dosyalar:** `api/main.py`, `src/serialization.py`, `tests/test_api.py`
BODY

mk "B-12 · Öğrenci id'leri log'dan ve hata gövdelerinden çıkar" "P1-pilot,area:api,privacy" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api` `privacy`

**Sorun**
`log_requests` (`api/main.py:80-92`) `request.url.path`'i yazıyor, yani
`GET /predict/STU300001` erişim log'una düz metin öğrenci id'si bırakıyor —
rotasyonsuz, maskesiz, çoğunlukla reşit olmayan kişilere ait.
`validate()` hata mesajları **10 gerçek id** ve tüm iç kolon listesini
döndürüyor (`validation.py:66-70`), bu da `api/main.py:227`'de 400 gövdesi
oluyor. `unhandled_exception_handler` `logger.exception` ile tam pandas
traceback'i yazıyor, istisna mesajlarında veri değerleri bulunabiliyor.

**Kabul kriterleri**
- [ ] Erişim log'unda path şablonu yazılıyor (`/predict/{student_id}`), değer yok
- [ ] `validate()` detayları log'a gidiyor, client'a generic mesaj dönüyor
- [ ] Log formatında `request_id` var (erişim satırı ile traceback
      eşleştirilebiliyor)
- [ ] `docs/` içinde log saklama süresi notu

**Dosyalar:** `api/main.py`, `src/data/validation.py`, `src/logging_setup.py`
BODY

mk "B-13 · Yedekleme: `pg_dump` + zamanlama + geri yükleme prosedürü" "P1-pilot,area:ops" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ops`

**Sorun**
Hiçbir yedek yolu yok. `daily_students` CSV'den yeniden kurulabilir ama
**`alerts` hiçbir yerden geri gelmiyor** — kimin ne zaman hangi olasılıkla
işaretlendiğinin tek kaydı o ve ürünün tüm değer iddiası ona dayanıyor. README
iki yerde sorun giderme adımı olarak `down -v` öneriyordu (düzeltildi ama script
yok).

**Kabul kriterleri**
- [ ] `scripts/backup_db.sh` — `pg_dump`, tarihli dosya, N günlük saklama
- [ ] `scripts/restore_db.sh` + README'de prosedür
- [ ] Zamanlanmış (bkz. B-14) ve başarısızlıkta bildirim
- [ ] Geri yükleme **bir kez test edilmiş** ve sonucu README'ye yazılmış
      (test edilmemiş yedek yedek değildir)

**Dosyalar:** `scripts/`, `docker-compose.yml`, README
BODY

mk "B-14 · Zamanlayıcı servisi + koşu başarısızlığında bildirim" "P1-pilot,area:ops" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ops`

**Sorun**
"Günlük erken uyarı sistemi" kendi kendini çalıştırmıyor: günlük koşu, kullanıcının
elle eklemesi gereken bir cron satırı. Ayrıca `send_daily_alerts.py` hata
durumunda 1 dönüyor ama **kimse izlemiyor** — pipeline sessizce ölürse kimse
fark etmiyor.

**Kabul kriterleri**
- [ ] Compose'a bir zamanlayıcı servisi (ör. `ofelia` ya da basit bir cron
      container'ı): 09:00 pipeline → alerts, `&&` ile zincirli
- [ ] Koşu başarısız olursa **bize** bildirim (ayrı bir Telegram kanalı /
      webhook), müşterinin kanalına değil
- [ ] `runs` tablosuna `status='failed'` yazılıyor (B-04)
- [ ] 24 saattir başarılı koşu yoksa uyarı (heartbeat)
- [ ] README'de zamanlama bölümü compose tabanlı

**Dosyalar:** `docker-compose.yml`, `scripts/`, README
BODY

mk "B-15 · CI'a Postgres servisi — DB testleri gerçekten koşsun" "P1-pilot,area:ops" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ops`

**Sorun**
`.github/workflows/ci.yml` sadece `compileall` + `pytest` koşuyor. Postgres
servisi olmadığı için `TEST_DATABASE_URL` boş, `tests/test_db_integration.py`
içindeki 9-12 test `pytest.skip` ile **her PR'da sessizce atlanıyor** ve
`addopts = -q` yüzünden çıktı yeşil görünüyor. Yani `db/schema.sql` veya bir
migration hiçbir zaman CI'da çalıştırılmıyor; bir yazım hatası merge olur ve
müşteri makinesinde `init` servisini düşürür — bu da `api`'nin hiç başlamaması
demektir.

**Kabul kriterleri**
- [ ] `services: postgres:18` + `TEST_DATABASE_URL`
- [ ] `pytest -ra` (atlananlar görünür olsun)
- [ ] `docker build` adımı
- [ ] `ruff` (kodda `# noqa: BLE001` var, yani lokalde kullanılıyor ama CI'da yok)
- [ ] `on: pull_request` + `concurrency` + `timeout-minutes`

**Dosyalar:** `.github/workflows/ci.yml`, `pytest.ini`, `requirements-dev.txt`
BODY

mk "B-16 · Migration sürüm tablosu + `001`'in idempotent olmaması" "P1-pilot,area:db" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db`

**Sorun**
`init_db.py` her koşuda `schema.sql` + `db/migrations/*.sql`'in **tamamını**
yeniden uyguluyor, sürüm tablosu yok. Yani doğruluk, her migration yazarının
sonsuza dek idempotent SQL yazmasına bağlı.

`001_churn_probability_to_double.sql` bunu sağlamıyor: `ALTER COLUMN ... TYPE`
her koşuda `alerts` üzerinde `ACCESS EXCLUSIVE` kilit alıyor ve o kolona bağlı
bir **view** oluştuğu an tamamen hata veriyor. Tek transaction olduğu için her
şey geri alınıyor, `init` non-zero çıkıyor ve `api`
`service_completed_successfully` beklediği için **hiç başlamıyor**. Aynı şekil
`ADD COLUMN` (IF NOT EXISTS'siz), `RENAME`, `CREATE TYPE`, `ADD CONSTRAINT` için
de geçerli — yani gelecekteki her migration bu tuzağa aday.

**Kabul kriterleri**
- [ ] `schema_migrations(version, applied_at)` tablosu; uygulanan migration
      tekrar koşmuyor
- [ ] `001` ya sürüm tablosuyla korunuyor ya da guard'lı hale getiriliyor
- [ ] Dosya adı konvansiyonu (`NNN_`) CI'da kontrol ediliyor
- [ ] Test: bir view ekledikten sonra `init_db.py` başarılı

**Dosyalar:** `scripts/init_db.py`, `db/migrations/`, CI
BODY

mk "B-17 · Container hijyeni: dockerignore, non-root, sabitlenmiş base" "P1-pilot,area:ops,security" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ops` `security`

**Sorun**
- `.dockerignore` `data/`, `RnD/`, `notebooks/`, `*.ipynb`, `*.pt` içermiyor ve
  `Dockerfile:13` `COPY . .`. `.gitignore` `data/*.csv`'yi bilerek commit'liyor,
  yani **gerçek müşteri exportu geldiği gün her imaj o öğrenci kayıtlarını
  taşır.** İmaj bir kez elden çıktığında geri dönüş yok.
- `.env*` glob'u yok; sadece `.env` ve `.env.docker` listeli. `.env.local` /
  `.env.staging` imaja girer, `docker history` ile SMTP şifresi ve bot token'ı
  okunur.
- `USER` yok, root koşuyor. `HEALTHCHECK` imajda değil compose'da (müşteri
  `docker run`/k8s ile koşarsa liveness sinyali yok).
- `FROM python:3.13-slim` digest'siz.

**Kabul kriterleri**
- [ ] `.dockerignore`: `.env*`, `data/`, `RnD/`, `notebooks/`, `*.ipynb`, `*.pt`,
      `.pytest_cache`, `.ruff_cache`
- [ ] `useradd` + `USER app`
- [ ] `HEALTHCHECK` Dockerfile'da
- [ ] Base image digest ile sabitlenmiş
- [ ] `docker history <img> | grep -i env` temiz olduğunu gösteren bir kontrol

**Dosyalar:** `.dockerignore`, `Dockerfile`
BODY

mk "B-18 · KVKK: silme / anonimleştirme yolu" "P1-pilot,area:db,privacy" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db` `privacy`

**Sorun**
`alerts.student_id` `ON DELETE` politikası olmayan bir foreign key ve hiçbir yer
`daily_students`'tan silmiyor. Bir silme talebi geldiğinde tek yol öğrencinin
alarm geçmişini yok etmek (denetim izi gider) ya da uyumsuz kalmak. Saklama
süresi tanımlı değil, anonimleştirme rutini yok.

**Kabul kriterleri**
- [ ] `scripts/forget_entity.py <id>` — iki mod: `--anonymize` (id'yi geri
      döndürülemez bir takma adla değiştir, geçmişi koru) ve `--purge` (tümünü
      sil), ikisi de tek transaction
- [ ] Yaptığı işi bir `erasure_log`'a yazıyor (ne zaman, hangi mod, kaç satır)
- [ ] FK'lerde açık `ON DELETE` politikası
- [ ] `docs/` içinde saklama süresi ve prosedür
- [ ] Test

**Dosyalar:** `scripts/`, `db/schema.sql`, migration, `docs/`
BODY

mk "B-19 · `daily_students` budama / `is_active`" "P1-pilot,area:db" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:db`

**Sorun**
`upsert_daily_students_db` bugünün dosyasında olmayan öğrencileri **hiç
dokunmadan bırakıyor** (`loader.py:166`, bilinçli — FK geçmişi korumak için). Ama
seçilen çözüm onları skorlamaya devam etmek. `load_daily_students_db` tabloyu
koşulsuz `SELECT` ediyor.

Sonucu: programı bitiren 300 öğrenci donmuş feature değerleriyle kalıyor,
`days_since_last_contact` hiç güncellenmediği için oldukları yerde duruyor, model
onları skorlamaya devam ediyor ve bazıları eşiği geçiyor. Mentora "iki ay önce
mezun olan öğrenciyi ara" listesi gidiyor — ve hiçbir şey değişmediği için **her
gün**.

**Kabul kriterleri**
- [ ] `is_active` (ya da B-03'teki `as_of_date` ile "bugünün dosyasında olanlar")
- [ ] `load_daily_students.py --replace`: dosyada olmayanları pasif işaretliyor
- [ ] Skorlama sadece aktifleri alıyor
- [ ] Pasife alınanlar log'lanıyor ve sayısı raporlanıyor
- [ ] Test

**Dosyalar:** `src/data/loader.py`, `scripts/load_daily_students.py`,
`db/schema.sql`
BODY

mk "B-20 · Hazırlık (readiness) doğru olsun; kalibratör eksikse fail-closed" "P1-pilot,area:api,area:ml" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:api` `area:ml`

**Sorun**
`lifespan` (`api/main.py:58-66`) `app.state.model`'i **explainer, kalibratör ve
meta'dan önce** atıyor. Üçünden biri hata verirse `model` zaten dolu, yani
`/health` `{"status":"ok"}` dönüyor ama her skorlama çağrısı 500 veriyor.
`docker-compose.yml:77` healthcheck olarak `/health` kullandığı için kalıcı
bozuk bir instance sonsuza dek "healthy" kalıyor.

Daha sessiz olanı: `load_calibrator` `FileNotFoundError`/`OSError` yutup `None`
dönüyor (`calibrate.py:87-92`) ve `churn_proba` **ham** CatBoost skorunu
döndürüyor. `auto_class_weights="Balanced"` ile ham çıktı olasılık değil (ham
Brier 0.203, kalibre 0.173). Eşik hâlâ kalibre skorlar üzerinde seçilmiş 0.29.
Yani mentora giden liste kat kat uzuyor, her olasılık yanlış, ve hiçbir yerde
yazmıyor.

**Kabul kriterleri**
- [ ] Yükleme sırası: hepsi yüklenmeden `app.state.model` set edilmiyor
- [ ] `/health` model + explainer + kalibratör + meta'nın hepsini kapsıyor
- [ ] Kalibratör yoksa: ya başlatma reddediliyor ya da `/health` `degraded` ve
      skorlama endpoint'leri 503
- [ ] `CALIBRATION_METHOD` set iken kalibratör yokluğu **sessiz geçilmiyor**
- [ ] `meta["features"] == FEATURES` ve `meta["cat_cols"] == CAT_COLS` yüklemede
      assert ediliyor (sıra değişmişse SHAP etiketleri yanlış feature'a bağlanır)
- [ ] Test: kalibratör dosyası yokken servis kalkmıyor / degraded

**Dosyalar:** `api/main.py`, `src/model/calibrate.py`, `pipeline/daily_pipeline.py`
BODY

mk "B-21 · Sızıntı denetimi: iletişim feature'ları ve `days_to_next_exam`" "P1-pilot,area:ml" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ml`

**Sorun**
`compare_feature_sets.py` ölçümü: tüm 24 feature PR-AUC 0.512, o iki iletişim
kolonu olmadan 0.380, tek kolon `days_since_last_contact` 0.486. Yani sinyalin
neredeyse tamamı mentor davranışı kolonlarında.

**Risk ters nedensellik:** "mentorun son temasından geçen gün" kısmen çoktan
gerçekleşmiş kopuşu ölçüyor — zihnen ayrılan öğrenci cevap vermez, mentor aramayı
bırakır. Bu doğruysa model geçmişi bildiriyor, geleceği uyarmıyor. Ve **ürün
doğru kullanıldıkça bozuluyor**: "herkesle 7 günde bir temas" politikası
uygulanınca kolon tüm kohortta düzleşiyor ve model herkese düşük risk veriyor.

Ayrıca `days_to_next_exam` `grade`'in tam bir fonksiyonu (ölçüldü: `11. Sınıf`
661-668, `12. Sınıf` 297-303, `Mezun` 297-302) — `monthly_fee_try` için
düzeltilen aynı tekrar-bilgi hatası.

**Kabul kriterleri**
- [ ] Her feature için "churn penceresinden önce biliniyor muydu" denetimi
      yazılı, `docs/` içinde tablo
- [ ] İletişim kolonları **pencere başlangıcından önceki** değerle hesaplanıyor
      (lagged); `features.py`'de açık bir lag mekanizması
- [ ] `days_to_next_exam` düşüyor ya da `grade`'e göre normalize ediliyor
- [ ] Lagged sürümle PR-AUC yeniden ölçülüyor ve README'ye yazılıyor (düşecek —
      düşmesi doğru olan)
- [ ] `compare_feature_sets.py` sonuçları repoda saklanıyor

**Dosyalar:** `src/data/features.py`, `config.py`,
`scripts/compare_feature_sets.py`, README, `docs/`
BODY

mk "B-22 · Temiz split'ler: 4. dilim, imputer split sonrası, zamana göre bölme" "P1-pilot,area:ml" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ml`

**Sorun** (üçü aynı yerde)

1. **Validation seti üç iş yapıyor:** early stopping (`use_best_model`,
   `catboost_tree_count: 145/300` tetiklendiğini doğruluyor), kalibratör fit'i,
   ve eşik seçimi. Bedeli `model_meta.json`'da ölçülü: aynı 0.29 eşiğinde
   validation maliyeti 321 / recall 0.685, **test maliyeti 376 (+%17) / recall
   0.571.** Meta'da saklanan `expected_cost` bu yüzden iyimser.
2. **Imputer split'ten önce fit ediliyor:** `build_training_frame` tüm frame'de
   `fit_imputation` çağırıyor, split sonra geliyor. `satisfaction_survey_score`
   günlük örnekte %28 null, yani nadir bir yol değil. 200 satırlık bir müşteri
   pilotunda etkisi ciddi.
3. **Rastgele stratified split:** `enrollment_date` var ve feature'lardan bilerek
   çıkarılmış; veri tek bir snapshot ve kohort yapısı taşıyor. Erken uyarı
   ürününde holdout **tarihe göre** olmalı.

**Kabul kriterleri**
- [ ] `train / es-val / calib-val / test` (ya da iç içe): early stopping ayrı
      dilimde, kalibrasyon + eşik ayrı dilimde
- [ ] `fit_imputation(X_train)` — öğrenilen dict split'ten sonra üretiliyor
- [ ] `split_by_date()` eklenip varsayılan hale geliyor; `preprocess.py`'deki
      rastgele split sadece opt-in
- [ ] `model_meta.json`'a split boyutları, tarih sınırları, seed'ler ve kütüphane
      sürümleri yazılıyor
- [ ] Yeni sayılar README'ye

**Dosyalar:** `pipeline/training_pipeline.py`, `src/data/preprocess.py`,
`src/data/features.py`, `src/model/train.py`, README
BODY

mk "B-23 · Eşik ile mentor kapasitesini uzlaştır" "P1-pilot,area:ml,product" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ml` `product`

**Sorun**
0.29 eşiğinde model 677 öğrenciden 244'ünü (**%36**) işaretliyor, ama
`PRECISION_AT_K = 20` "mentor koşu başına 20 kişiyle konuşabilir" diyor. 12 kat
fark. `DECISION_COST` sınırsız outreach kapasitesi varsayıyor (yanlış alarm
kaçıncı olursa olsun sabit 1 birim). İkisi birden doğru olamaz ve pratikte
mentorlar listenin tepesini çalışıyor — yani gerçek iş akışını tanımlayan sayı
`precision@20`.

Ayrıca `DECISION_COST = 3:1` gerekçesiz: kaybedilen öğrenci 1.350-1.950 TL/ay ×
kalan süre, yanlış alarm 5-10 dakika mentor zamanı. Bu 50:1'e daha yakın ve eşiği
belirgin aşağı çeker.

**Kabul kriterleri**
- [ ] `select_threshold`'a kapasite kısıtı: `capacity_per_run`'dan fazla
      işaretlemeyen en iyi eşik
- [ ] `DECISION_COST` gerekçesi config yorumunda sayısal olarak yazılı
- [ ] Ties: argmin platosunun **ortası** seçiliyor (bugün strict `<` ile en
      küçük, yani en agresif kenar seçiliyor ve küçük dağılım kaymalarında liste
      zıplıyor)
- [ ] `model_meta.json`'da hem maliyet-optimal hem kapasite-kısıtlı eşik
- [ ] README'de precision@K öne çıkarılıyor

**Dosyalar:** `src/model/threshold.py`, `config.py`,
`pipeline/training_pipeline.py`, README
BODY

mk "B-24 · Model sürümleme, rollback ve drift kontrolü" "P2,area:ml,area:ops" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ml` `area:ops`

**Sorun**
`MODEL_PATH` sabit bir dosya adı, retrain modeli **yerinde eziyor**, geri dönüş
yok, hangi modelin serve edildiği bir yerde yazmıyor. `model_meta.json` da
üzerine yazıldığı için geçmiş alarmların provenance'ı retrain'de yok oluyor.

Ayrıca imputation medyanları eğitimde donuyor ve servis anındaki dağılımla hiç
karşılaştırılmıyor: bugün medyan 9 saat, 6 ay sonra gerçek medyan 6 saat olsa
bile her eksik değer hâlâ 9 ile doldurulur — tam da flag'in tespit etmek için
var olduğu popülasyonu sistematik olarak daha çalışkan gösterir.

**Kabul kriterleri**
- [ ] `saved_models/<slug>/<version>/` + `current` sembolik bağlantısı
- [ ] `model_version` alarm satırlarına yazılıyor (B-05)
- [ ] `scripts/rollback_model.py`
- [ ] `scripts/check_drift.py`: servis dağılımı vs eğitim medyanları, null oranı
      değişimi, eşik üstü oran değişimi — eşik aşılırsa uyarı
- [ ] Retrain cadence'ı `docs/` içinde yazılı

**Dosyalar:** `src/model/save.py`, `src/model/load.py`, `config.py`, `scripts/`
BODY

mk "B-25 · Bildirim dayanıklılığı" "P2,area:notify" "backlog" <<'BODY'
**Etiketler:** `P2` `area:notify`

**Sorun** (yedisi bir arada, hepsi `channels.py` / `message.py`)

1. **Retry yok, `Retry-After` yok.** 429/502 ya da 15 saniyelik bir ağ takılması
   günün alarmını tamamen kaybettiriyor; tek deneme var.
2. **4096 aşımında sessiz kırpma** ve sonuç `"sent"` raporlanıyor. Ölçüldü:
   varsayılan config 3594 karakter (%12 pay). `SHAP_TOP_N_FEATURES` 3→4 olunca
   4204 → sessizce kırpılıyor ve "... ve N öğrenci daha" satırı da gidiyor.
3. **Idempotency yok.** Telegram 429 verip e-posta başarılı olursa exit 1, bir
   wrapper retry ederse mentorlara ikinci e-posta gidiyor.
4. **Başlıkta `datetime.now()`**, raporlanan koşunun `run_at`'i değil
   (`message.py:171`). `--force` ile 40 saatlik bir koşu bugünün tarihiyle
   çıkıyor.
5. **Boş koşuda bayat gönderim:** 24 saat guard'ı zamanlayıcı jitter'ıyla
   geçilebiliyor; bugün kimse riskli değilse dünün öğrencileri bugünün başlığıyla
   gidiyor (B-04'e bağlı).
6. **`_headline_reason` all-negative fallback'i** riski *azaltan* bir gerekçeyi
   yön işareti olmadan basıyor (`message.py:110`) — docstring'in tam tersini
   yapıyor. Ve `impact > 0` testi `0.0`, `-0.0` ve `NaN`'ı "riski azaltıyor"
   sayıyor; `+0.004` CSV'den `+0.00` olarak dönüp "azaltıyor" diye raporlanıyor.
7. **Türkçe ondalık virgül yok** (`2.1` yerine `2,1`) — dosyadaki tek
   lokalize edilmemiş yer.

**Kabul kriterleri**
- [ ] 3 denemeli exponential backoff + `Retry-After` desteği
- [ ] Uzunluk bütçesi `build_message`'a taşınıyor (tam öğrenci düşürüyor ve
      sayacı düzeltiyor); `send_telegram` kırpmak yerine hata veriyor
- [ ] `alerts` ya da `runs` üzerinde `notified_at` — aynı koşu iki kez
      gönderilmiyor
- [ ] `run_at` mesaj başlığına geçiyor
- [ ] `impact` sınıflaması: `> tol` artırıyor, `< -tol` azaltıyor, arası nötr;
      NaN güvenli
- [ ] All-negative durumda açık ifade ("en güçlü etken riski azaltıyor")
- [ ] Türkçe ondalık virgül
- [ ] Test: tam kapasite mesaj (10 detay + 15 tekrar + en uzun etiketler) 4096
      altında kalıyor — bu, tüm kapama tasarımının var olma sebebi ve şu an test
      edilmiyor

**Dosyalar:** `src/notifications/channels.py`, `src/notifications/message.py`,
`src/notifications/notify.py`, `scripts/send_daily_alerts.py`, `tests/`
BODY

mk "B-26 · ML çekirdeği için test yok" "P2,area:ml" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ml`

**Sorun**
`test_notifications.py`'de ~28 test var; `src/model/` (eşik seçimi, kalibrasyon,
sabit-kalibratör yolu, evaluate) ve `src/data/features.py` (imputation reçetesi,
train/serve paritesi) için **hiç** test yok. `features.py:8` docstring'i
"reçete `data/updated_data.csv`'yi ham dosyadan birebir üretir" diyor — bu tek
satırlık bir test ve yok.

Ayrıca `calibrate.py:42-45`: tek sınıflı bir validation seti kalibratörü
**sabit** yapıyor ve eğitim tamamlanıp o modeli ship ediyor. Az churner'lı bir
müşteri pilotunda kalibratör sabit 0.0 olur, eşik 0.01 seçilir, ROC tam 0.5
çıkar ve tamamen makul görünen bir meta dosyası üretilir — sonra pipeline her gün
kimseyi işaretlemez, hatasız.

**Kabul kriterleri**
- [ ] `test_features.py`: reçete paritesi (ham → engineered birebir),
      missing-flag mantığı, imputation grup medyanları
- [ ] `test_threshold.py`: plato ortası seçimi, kapasite kısıtı, `>=` sınırı
- [ ] `test_calibrate.py`: tek sınıflı val **hata veriyor** (ya da meta'ya
      `degraded: true` yazıyor), monotonluk, log-odds fit'i
- [ ] `test_evaluate.py`: `precision_at_k` k'dan az satırda ne yapıyor (bugün
      sessizce 12 satırın ortalamasını alıp `precision_at_20` diye etiketliyor)
- [ ] `build_model(random_state=7)` `TypeError` veriyor — düzeltilip test ediliyor

**Dosyalar:** `tests/`, `src/model/`, `src/data/features.py`, `src/model/model.py`
BODY

mk "B-27 · Platt kalibrasyonu log-odds üzerinde fit edilmeli" "P2,area:ml" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ml`

**Sorun**
`src/model/calibrate.py:35,39,46` lojistik regresyonu **olasılık** üzerine fit
ediyor (`predict_proba[:,1]` ∈ [0,1]), Platt scaling ise `σ(a·f + b)`'yi
*decision function* üzerine fit eder. Sınırlı bir girdinin lojistiği identity
map'i temsil edemez ve kuyrukları yapısal olarak sıkıştırır; `C=1e10` ile
neredeyse regularizasyon olmadığı için `lbfgs` katsayıyı çok büyütüp doyabilir ya
da yakınsamayabilir (kontrol edilmiyor).

**Kabul kriterleri**
- [ ] Fit `log(p/(1-p))` üzerinde (clip'li), ya da
      `sklearn.calibration._SigmoidCalibration`
- [ ] Yakınsama kontrol ediliyor, uyarı log'lanıyor
- [ ] Brier ve kalibrasyon eğrisi öncesi/sonrası karşılaştırması test'te

**Dosyalar:** `src/model/calibrate.py`, `tests/test_calibrate.py`
BODY

mk "B-28 · Satır bazlı karantina — tek null tüm koşuyu düşürmesin" "P2,area:ml,area:api" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ml` `area:api`

**Sorun**
`require_no_nulls` tüm frame için hata veriyor (`validation.py:81-87`), API bunu
400'e çeviriyor. 25.000 öğrenciden birinde upstream yarım kalmış bir job
yüzünden null varsa **hiç kimse skorlanmıyor**, alarm gitmiyor, ve arıza bir
cron job'unda 400 olarak görünüyor. Bir batch skorlayıcının doğru davranışı:
iyi satırları skorla, reddedilenleri raporla.

**Kabul kriterleri**
- [ ] `score_students` reddedilen satırları ayırıyor ve
      `(scored_df, rejected_df)` döndürüyor
- [ ] Reddedilenlerin sayısı ve sebebi log'lanıyor, `runs` tablosuna yazılıyor
- [ ] API yanıtında `skipped_count`
- [ ] Reddedilen oranı bir eşiği aşarsa koşu yine de hata veriyor
- [ ] Test

**Dosyalar:** `pipeline/daily_pipeline.py`, `src/data/validation.py`,
`api/main.py`
BODY

mk "B-29 · Template: `clients/` iskeleti + `ClientProfile`" "P2,template,area:ml" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:ml`

Ayrıntı: `docs/TEMPLATE_PLAN.md` Adım 1.

**Kabul kriterleri**
- [ ] `clients/_base.py`: `ClientProfile` dataclass + `validate_profile()`
      (mevcut `_validate_feature_config()` buraya taşınıp genişletiliyor)
- [ ] `clients/edu_demo/profile.py`: bugünkü `config.py` içeriği
- [ ] `config.py` yükleyiciye dönüşüyor (`EOAI_CLIENT`), geriye dönük takma adlar
      geçiş için kalıyor
- [ ] `pytest` yeşil, `demo_up.sh` çalışıyor, `config.py` ~40 satır
BODY

mk "B-30 · Template: testler profil fixture'ına bağlanmalı" "P2,template" "backlog" <<'BODY'
**Etiketler:** `P2` `template`

**Sorun**
`test_loader.py`, `test_notifications.py`, `test_db_integration.py` EO kolon
adlarına bağlı, `conftest.py` `RAW_DATA_PATH`'i yüklüyor. Yeni bir dikeyde suite
ve dolayısıyla CI **ilk gün** patlıyor.

**Kabul kriterleri**
- [ ] `clients/_test/profile.py`: 6 feature'lı minimal sentetik profil
- [ ] `conftest.py` `EOAI_CLIENT=_test` ile koşuyor
- [ ] EO kolon adlarına bağlı assert'ler profilden okunuyor
- [ ] Testler EO verisi olmadan geçiyor
BODY

mk "B-31 · Template: entity soyutlaması" "P2,template,area:db" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:db`

Ayrıntı: `docs/TEMPLATE_PLAN.md` Adım 3 ve Bölüm 5. **Dinamik SQL
kullanılmıyor** — DB kolonları generic oluyor, eşleme sınırda yapılıyor.

**Kabul kriterleri**
- [ ] Migration: `student_id` → `entity_id`, `enrollment_date` → `enrolled_at`
- [ ] `to_internal(df, profile)` / `to_external(records, profile)`
- [ ] `loader.py`'daki SQL generic ve statik
- [ ] `init_db.py` `EXPECTED` generic; `table_schema='public'` sabiti yerine
      `search_path`'e dayanmayan bir kontrol
- [ ] `entity_id_column = "ogrenci_no"` olan bir profil kod değişikliği olmadan
      uçtan uca çalışıyor
BODY

mk "B-32 · Template: `GET /schema` endpoint'i" "P2,template,area:api" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:api`

**Sorun**
Dashboard `adapters.js`'de `FEATURE_LABELS`'ın **kendi kopyasını** tutuyor ve
kaçınılmaz olarak `config.py`'den ayrışacak. Etiketler API'den gelmeli.

**Kabul kriterleri**
- [ ] `GET /schema`: `display_name`, `entity` (`id_field`, `noun`,
      `noun_plural`), `fields[]` (`name`, `label`, `type`, `unit`, `is_flag`),
      `capacity_per_run`, `warning_window_days`
- [ ] `API_CONTRACT.md` güncel
- [ ] Test: profil etiketi değişince `/schema` çıktısı değişiyor
BODY

mk "B-33 · Template: mesaj sözlüğü, türetim kaydı ve kalan sabitler" "P2,template" "backlog" <<'BODY'
**Etiketler:** `P2` `template`

**Kabul kriterleri**
- [ ] `clients/<slug>/strings.py`: tüm Türkçe literaller, entity ismi, yön
      ifadeleri
- [ ] `clients/<slug>/derive.py` + profil `derivations` listesi
      (`add_monthly_value` artık kaynaklı değil)
- [ ] `is_synthetic_data` profilden (bugün `True` sabit)
- [ ] Model yolu `saved_models/<slug>/model.cbm`
- [ ] `single_rule_baseline` default kolonu ve `compare_feature_sets.py`
      `CONTACT_FEATURES` profilden
- [ ] Kabul: entity ismini "abone" yapan bir profil "8 abone risk altında"
      üretiyor
BODY

mk "B-34 · Template: `new_client.py` scaffolder + `docs/ONBOARDING.md`" "P2,template,area:docs" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:docs`

**Kabul kriterleri**
- [ ] `python scripts/new_client.py <slug> --csv <path>`: `clients/<slug>/`
      iskeletini kuruyor, CSV kolonlarından `features` taslağı üretiyor,
      doldurulacakları `TODO` olarak işaretliyor
- [ ] `docs/ONBOARDING.md`: yeni CSV'den çalışan sisteme komut komut
- [ ] Kabul testi: yeni bir dikey **2 saat** içinde, `core/`'a dokunmadan
BODY

mk "B-35 · Template: ikinci dikeyi gerçekten yap (OULAD)" "P2,template,area:ml" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:ml`

**Sorun**
Kanıtlanmamış template iddiası toplantıda çöker. Ayrıca sentetik veri problemini
de aynı işte çözüyor: [OULAD](https://archive.ics.uci.edu/dataset/349/open+university+learning+analytics+dataset)
32.000+ gerçek öğrenci, **günlük** VLE tıklama akışı, `Withdrawn` etiketi, CC BY
4.0. Trend özellikleri ve tarihe göre split ilk kez gerçek veride kurulabiliyor.

**Kabul kriterleri**
- [ ] `clients/oulad/` profili, `core/`'a dokunmadan
- [ ] Trend özellikleri (son 7/14/28 gün etkinlik deltaları)
- [ ] Tarihe göre split, precision@N raporu
- [ ] Süreç kronometreyle ölçülüyor; `core/`'a dokunmak zorunda kalınan her yer
      not edilip ayrı issue açılıyor
- [ ] README'de "32.000 gerçek öğrenci üzerinde ölçülen" cümlesi ve sayısı
BODY

mk "B-36 · `docs/DEMO_SETUP.md` — ekip arkadaşı için kurulum sayfası" "P2,area:docs" "backlog" <<'BODY'
**Etiketler:** `P2` `area:docs`

**Kabul kriterleri**
- [ ] Klon + Docker yolu, kopyala-yapıştır
- [ ] Bilinen 5 tuzak: Windows'ta WSL2 şart, `sed -i ''` macOS sözdizimi,
      port çakışmaları (`ALLOWED_ORIGINS` / `VITE_API_BASE` eşlemesi dahil),
      dashboard klasör adının tam olması, private repo erişimi
- [ ] İmaj tarball'ı alternatifi + mimari uyarısı (arm64 tar x86'da çalışmaz)
- [ ] Tailscale ile "kimse kurmasın" seçeneği
BODY

mk "B-37 · `RnD/` ayıklaması" "P2,area:ops" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ops`

**Sorun**
`RnD/` hiçbir yerden import edilmiyor, CI'ın `compileall`'ına dahil değil,
dokümanlarda hiç geçmiyor ve imaja giriyor. `model_2.py` dört ayrı hatayla
**import bile edilemiyor** (`src.data.Validation` büyük harfle, `preprocess`'te
olmayan bir sembol, `list + str`, yanlış `evaluate_model` imzası).
`model_3.py` zaten engineered olan `updated_data.csv` üzerine
`build_training_frame` çağırıyor, yani `_missing` flag'lerini yeniden hesaplayıp
sıfırlıyor — dolayısıyla commit'li "ANN vs CatBoost" karşılaştırması **farklı
feature'lar üzerinde** hesaplanmış. Ayrıca `saved_models/`'a `.pt` ve
`metrics/`'e json yazıyor.

**Kabul kriterleri**
- [ ] `RnD/` ayrı bir `eoai-research` repo'suna taşınıyor ya da siliniyor
- [ ] `saved_models/model_3_ann_*.pt` ve `metrics/model_3_metrics_*.json`
      repodan çıkarılıyor
- [ ] `.gitignore` `metrics/*.json`'a genişletiliyor
- [ ] Taşınmıyorsa: `.dockerignore`'a giriyor, CI'a dahil ediliyor, README'de
      "desteklenmiyor" notu
BODY


say ""
say "== Ozet =="
say "  olusturulan: $CREATED"
say "  atlanan:     $SKIPPED"
if [ "$DRY_RUN" = 1 ]; then
  say ""
  say "Bu bir dry-run'di. Gerceklestirmek icin --dry-run olmadan calistir."
fi
exit 0
