#!/usr/bin/env bash
#
# Eo-Churn-Dashboard-demo-Edu issue'larini olusturur
#
# Kullanim:
#   ./create_dashboard_issues.sh                 -> issue'lari olusturur
#   ./create_dashboard_issues.sh --dry-run       -> sadece ne yapacagini yazar, hicbir sey olusturmaz
#   ./create_dashboard_issues.sh owner/repo      -> baska bir repoyu hedefler (varsayilan: bulundugun repo)
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

mk "D-01 · Risk renk bantları eşikle ilişkisiz — listenin tepesi İZLEMEDE görünüyor" "P0-demo,area:ui" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ui`

**Sorun**
`src/theme.js:32-37` `riskColor` / `riskLevel` sabit eşiklerle çalışıyor:
KRİTİK > 0.6, YÜKSEK ≥ 0.4, altı İZLEMEDE. Modelin gerçek karar eşiği ise
`model_meta.json`'da **0.29** ve `App.jsx` onu backend'den doğru şekilde okuyor.

Sonucu: sigmoid kalibre bir modelde 0.29 eşiğiyle risk listesinin büyük kısmı
0.29-0.40 arasına düşüyor. "Günlük Risk Listesi · eşiği aşan öğrenciler"
başlığının altında **1 numaralı öğrenci sarı "İZLEMEDE"** badge'i taşıyor ve
`RiskListScreen.jsx:121`'deki legend **"> %60 kritik: 0"** yazıyor.
`DetailDrawer.jsx:92,162` ayrıca "%60 kritik eşiği" diye bir çizgi çiziyor.

Müşterinin ilk sorusu: "günlük arama listemde niye hiç kritik öğrenci yok?"

**Kabul kriterleri**
- [ ] Bantlar `chosenThreshold`'dan türetiliyor (ör. `t`, `t×1.5`, `t×2`) ya da
      tamamen kaldırılıyor
- [ ] `DetailDrawer`'daki sabit "%60 kritik eşiği" çizgisi gerçek eşiği gösteriyor
- [ ] Legend'daki sayaçlar aynı bantları kullanıyor
- [ ] Eşik 0.29 iken listenin ilk öğrencisi "KRİTİK" görünüyor

**Dosyalar:** `src/theme.js`, `src/components/RiskListScreen.jsx`,
`src/components/DetailDrawer.jsx`
BODY

mk "D-02 · Model Sağlığı'ndaki DEMO paneli modelde olmayan bir feature'ı 1 numara gösteriyor" "P0-demo,area:ui" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ui`

**Sorun**
`ModelHealthScreen.jsx:113-155` iki DEMO panelini (eğitim koşusu trendi + global
feature importance) `buildDataset()`'ten gelen uydurma veriyle **koşulsuz**
render ediyor. Üstelik `imp` eşlemesi `f.label = f[0]` yapıyor, yani
`data.js`'in sağladığı Türkçe etiketi değil **anahtarı** gösteriyor: tamamen
Türkçe bir arayüzde `engagement_score`, `trial_exam_score_trend`,
`days_since_last_contact` yazıyor.

Ve `engagement_score` **`config.FEATURES`'ta yok** — modelde böyle bir feature
hiç olmadı. Müşteri o slaytı fotoğraflayıp "engagement_score nedir" diye sorarsa
dürüst cevap "uydurma".

Ayrıca API başarısız olduğunda bile bu paneller render ediliyor: kırmızı "GET
/metrics başarısız" satırının hemen altında 0.78→0.84 tırmanan sağlıklı
görünümlü bir ROC-AUC trendi duruyor.

**Kabul kriterleri**
- [ ] Uydurma feature importance paneli **kaldırılıyor** (backend gerçek
      importance dönene kadar)
- [ ] Eğitim koşusu trendi de kaldırılıyor ya da `metricsError` varken render
      edilmiyor
- [ ] Kalan DEMO içerik varsa etiketi Türkçe ve "DEMO" badge'i belirgin
- [ ] `engagement_score` string'i repoda hiç kalmıyor

**Dosyalar:** `src/components/ModelHealthScreen.jsx`, `src/data.js`
BODY

mk "D-03 · Gerçek `precision_at_20` ve `lift_at_20` gösterilmiyor" "P0-demo,area:ui" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ui`

**Sorun**
`/metrics` gerçekten `precision_at_20: 0.75`, `lift_at_20: 2.76` ve
`brier_score: 0.173` döndürüyor, `baseline_metrics` bloğu da dahil. `adapters.js:146-152`
bunların **hiçbirini** yüzeye çıkarmıyor. Yerine DEMO grafiği "precision@25"
diye bir şey çiziyor — backend'de `PRECISION_AT_K = 20`, yani @25 hiçbir yerde
yok.

`precision@20 = 0.75` ("ilk 20 kişiden 15'i gerçekten ayrılıyor") elimizdeki
**en ikna edici gerçek sayı** ve tam da `PRECISION_AT_K`'nın var olma sebebi olan
"mentor 20 kişi arayabilir" çerçevesi. Atılıp uydurma bir @25 çizgisi
gösteriliyor.

**Kabul kriterleri**
- [ ] `precision_at_{K}` ve `lift_at_{K}` kart olarak gösteriliyor, K dinamik
- [ ] `brier_score` ve `baseline_metrics` da yüzeye çıkıyor (model ne kadar
      iyi sorusunun dürüst cevabı)
- [ ] "precision@25" / "lift@25" ifadeleri repoda kalmıyor

**Dosyalar:** `src/adapters.js`, `src/components/ModelHealthScreen.jsx`
BODY

mk "D-04 · Detay panelinde iki buton hiçbir şey yapmıyor" "P0-demo,area:ui" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ui`

**Sorun**
`DetailDrawer.jsx:232-233` — "Telegram ile mentora bildir" ve "Not ekle",
birincil/ikincil olarak stillendirilmiş, `onClick` yok. Müşteri sahnede
"mentora Telegram'dan bildir"e basıyor ve hiçbir şey olmuyor: spinner yok, toast
yok, hata yok. Backend'de **çalışan** Telegram kodu olduğu için bu bozuk bir
entegrasyon gibi görünüyor.

**Kabul kriterleri**
- [ ] Ya ikisi de kaldırılıyor (demo öncesi hızlı yol)
- [ ] Ya da "Not ekle" → `POST /interventions` (B-06'ya bağlı) ve
      "Telegram ile bildir" → tek öğrenci için bildirim endpoint'i, başarı/hata
      geri bildirimiyle
- [ ] Ekranda `onClick`'siz hiçbir birincil buton kalmıyor

**Dosyalar:** `src/components/DetailDrawer.jsx`
**Bağımlılık:** B-06 (müdahale tablosu) — kaldırma seçeneği bağımsız
BODY

mk "D-05 · `/metrics` hatası, eşiğe ihtiyacı olmayan ekranı da boşaltıyor" "P0-demo,area:ui" "demo-ready" <<'BODY'
**Etiketler:** `P0-demo` `area:ui`

**Sorun**
`App.jsx:223` "Tüm Öğrenciler" ekranını `screen === 'all' && !esikYok` ile
gate'liyor. Oysa `AllStudentsScreen` eşiğin null olmasını **zaten doğru
yönetiyor** (`:23` ve `:36` — sadece "eşiği geçen" sayacını ve satır badge'ini
gizliyor).

Sonucu: `/metrics` 404 veriyor (model hiç eğitilmemişse tam bunu yapıyor) ya da
401 (anahtar açıksa), `/students` 25 öğrenciyi sorunsuz döndürüyor, ve müşteri
**boş bir sayfanın** üstünde kırmızı bir banner görüyor — oysa tam ve doğru
skorlanmış öğrenci listesi state'te render edilmeden duruyor.

**Kabul kriterleri**
- [ ] `!esikYok` gate'i "Tüm Öğrenciler"den kalkıyor
- [ ] Risk listesi eşik olmadan göstermemeye devam ediyor (bu karar doğru —
      yanlış ama inandırıcı bir liste boş ekrandan pahalı)
- [ ] Eşik yokken kullanıcıya hangi ekranın neden kısıtlı olduğu söyleniyor

**Dosyalar:** `src/App.jsx`
BODY

mk "D-06 · Header durum göstergesi kısmi hatada yeşil kalıyor" "P1-pilot,area:ui" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ui`

**Sorun**
`Header.jsx:21-25` sadece `error`'a (yani `/students` hatasına) bakıyor,
`metricsError`'a bakmıyor. `/metrics` 404 verip `/students` başarılı olduğunda
header **yeşil** "Model çalıştı" derken gövdede kırmızı "Model bilgisi
alınamadı" banner'ı duruyor. Tek ekranda iki çelişkili durum sinyali.

**Kabul kriterleri**
- [ ] Gösterge her iki hatayı kapsıyor; kısmi hatada sarı/uyarı durumu
- [ ] Üzerine gelince hangi çağrının başarısız olduğu görünüyor

**Dosyalar:** `src/components/Header.jsx`, `src/App.jsx`
BODY

mk "D-07 · İmpute edilmiş değerler ölçüm gibi gösteriliyor" "P1-pilot,area:ui" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ui`

**Sorun**
`weekly_study_hours_actual_missing` ve `satisfaction_missing` gerçek model
feature'ları ve API'nin döndürdüğü `features` objesinde **mevcut**
(`features` post-imputation frame, `daily_pipeline.py:91-93`). Ama
`adapters.js:83-96` ikisini de okumuyor ve `DetailDrawer.jsx:12-27`
`RAW_FIELDS`'ta yok. İkisinin Türkçe etiketi `adapters.js:33-34`'te kullanılmadan
duruyor.

Sonucu: ankete **hiç cevap vermemiş** bir öğrenci global impute değeri olan
`3.6` alıyor ve panelde "Memnuniyet puanı 3.6/5" olarak, gerçekten 3.56 veren
öğrenciyle **aynı renkte ve aynı ağırlıkta** görünüyor. Müşteri "bu öğrenci bize
ne zaman 3.6 verdi" diye soruyor, cevap "hiç".

**Kabul kriterleri**
- [ ] `_missing` flag'i 1 olan alan görsel olarak ayrışıyor (soluk + "veri yok,
      tahmini değer" ibaresi)
- [ ] Tooltip: değerin impute edildiği ve neyle doldurulduğu
- [ ] Kullanılmayan iki etiket kullanılıyor

**Dosyalar:** `src/adapters.js`, `src/components/DetailDrawer.jsx`
BODY

mk "D-08 · Veri kaynağı olmayan kolonlar ve kartlar sürekli `yok` yazıyor" "P1-pilot,area:ui" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ui`

**Sorun**
`adapters.js:100` `hist: null` sabit ve hiçbir endpoint alarm geçmişi
döndürmüyor. Sonucu:

- `RiskListScreen.jsx:50-51,59` — "Churn olasılığı" kolonunda her satırın
  yanında gri **"yok"**: ana ekranın ana tablosunda 18-25 kez tekrarlanan bir
  eksiklik ibaresi
- `DetailDrawer.jsx:79-87` — üç KPI kartından ikisi ("14 gün değişimi", "listede
  üst üste") **her zaman** "yok". Bir öğrencinin hikâyesini anlatmak için
  açtığın panel, üçte ikisi boş bir KPI satırıyla başlıyor
- `DetailDrawer.jsx:219` — sabit bir not: "API bu alanları döndürmüyor,
  daily_data.csv'de mevcut". Bu **artık doğru değil**; hemen altında canlı
  `features` değerleri duruyor. Müşteri "API bu alanları döndürmüyor" yazısını
  canlı alanlara bakarken okuyor ve dashboard'un mock olduğu sonucuna varıyor

**Kabul kriterleri**
- [ ] Delta kolonu ve iki KPI kartı, geçmiş endpoint'i gelene kadar
      **gizleniyor** (Trend ekranında yapıldığı gibi, bayrakla)
- [ ] `DetailDrawer.jsx:219`'daki yanlış not kaldırılıyor
- [ ] Ekranda hiçbir yerde "yok" tekrarı bir kolonun tamamını kaplamıyor

**Dosyalar:** `src/components/RiskListScreen.jsx`,
`src/components/DetailDrawer.jsx`, `src/adapters.js`
**Bağımlılık:** kalıcı çözüm B-03 + alarm geçmişi endpoint'i
BODY

mk "D-09 · API anahtarı desteği — nginx ters proxy" "P1-pilot,area:ui,security" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ui` `security`

**Sorun**
Backend `/health` dışındaki her endpoint'e `X-API-Key` şart koşuyor. `src/api.js`
bu header'ı **hiç göndermiyor** ve gönderecek bir ayar da yok. Sunucuda `API_KEY`
set edildiği an her iki istek 401 dönüyor, dashboard kırmızı
"API hatası: missing or invalid X-API-Key" banner'ı gösteriyor ve `esikYok` true
olduğu için **hiç içerik render etmiyor**.

Anahtarı bundle'a koymak (bir `VITE_API_KEY`) onu devtools'u açan herkese
yayınlamak demek — çok kullanıcılı bir pilotta bu kimlik doğrulama değil.

**Kabul kriterleri**
- [ ] `nginx.conf`'a `location /api/ { proxy_pass ...; proxy_set_header X-API-Key ...; }`
      — anahtar sunucu tarafında, container env'inden
- [ ] `VITE_API_BASE` varsayılanı `/api` oluyor: tarayıcı **aynı origin**'e
      istek atıyor
- [ ] Bunun yan faydası: CORS tamamen devre dışı kalıyor ve mixed-content sorunu
      (HTTPS sayfadan `http://localhost:8000`) ortadan kalkıyor
- [ ] README'de açıklanıyor

**Dosyalar:** `nginx.conf`, `Dockerfile`, `src/api.js`, README
**Bağımlılık:** B-07
BODY

mk "D-10 · `VITE_API_BASE` sessiz yanlış varsayılan" "P1-pilot,area:ops" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ops`

**Sorun**
`Dockerfile:18` `ARG VITE_API_BASE=http://localhost:8000`. `--build-arg`
verilmezse müşterinin tarayıcısı kalıcı olarak **kendi** localhost'una istek
atıyor. Değerin verildiğini doğrulayan bir build-time kontrolü yok. Ayrıca
varsayılan `http://`, yani dashboard HTTPS'ten servis edilirse her çağrı
mixed-content olarak bloklanıyor ve tarayıcı konsolu dışında hiçbir belirti yok.

Boş string özel bir tuzak: `api.js:6`'daki `??` nullish olduğu için `''`'i
yakalamıyor, `BASE` `''` oluyor, `GET /students?threshold=0` nginx'e gidiyor,
nginx SPA fallback ile 200 + HTML döndürüyor ve `res.json()`
"Unexpected token '<'" hatasını kullanıcıya ham olarak gösteriyor.

**Kabul kriterleri**
- [ ] Build, `VITE_API_BASE` boş ya da tanımsızsa **hata veriyor** (D-09
      sonrası varsayılan `/api` olacağı için bu daha da basitleşiyor)
- [ ] `api.js` boş string'i de yakalıyor
- [ ] JSON parse hatası kullanıcıya anlaşılır bir mesaj olarak dönüyor
- [ ] `nginx.conf`'ta `/assets/` dışı 404'ler HTML döndürmüyor (font ve favicon
      istekleri şu an 200 + HTML alıyor, bu yüzden eksik font sessizce sistem
      serif'ine düşüyor ve network sekmesinde 404 görünmüyor)

**Dosyalar:** `Dockerfile`, `src/api.js`, `nginx.conf`
BODY

mk "D-11 · `İletişime geçildi` işaretleri sayfa yenilemede sıfırlanıyor" "P1-pilot,area:ui" "pilot-ready" <<'BODY'
**Etiketler:** `P1-pilot` `area:ui`

**Sorun**
`App.jsx:43` `contacted` düz component state; hiçbir yere yazılmıyor. Mentor 20
öğrenciden 12'sini işaretliyor, F5 (ya da tarayıcı yeniden yüklemesi) ve 12
işaret ile sidebar'daki kapasite kartı sıfırlanıyor. "Yenile" butonu güvenli
(`load()` `contacted`'e dokunmuyor), yani problem tam olarak sayfa yenilemesi —
gergin bir sunucunun yaptığı ilk şey.

Ve daha önemlisi: müdahale kaydı pilotun ölçüm temeli (B-06). Kalıcı olmayan bir
işaret ölçüm değildir.

**Kabul kriterleri**
- [ ] `POST /interventions` ile sunucuya yazılıyor (B-06)
- [ ] O gelene kadar geçici olarak `localStorage` (try/catch'li)
- [ ] `AllStudentsScreen`'de de işaret **değiştirilebiliyor** (şu an satır
      vurgusunu gösteriyor ama toggle edemiyorsun — bir ekranda etkileşimli,
      diğerinde salt okunur)

**Dosyalar:** `src/App.jsx`, `src/components/AllStudentsScreen.jsx`
**Bağımlılık:** B-06
BODY

mk "D-12 · Template: etiketler `GET /schema`'dan gelsin" "P2,template,area:ui" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:ui`

**Sorun**
`adapters.js:10-35` Türkçe `FEATURE_LABELS`'ın kendi kopyasını, `:84-96` ise 14
alanı isimle eşleyen bir `adaptStudent`'ı, `DetailDrawer.jsx:12-27` de kendi
`RAW_FIELDS` listesini tutuyor. Üçü de `config.py`'den kaçınılmaz olarak
ayrışacak ve yeni bir dikeyde üçü de elle güncellenmek zorunda — yani dashboard
şu an template'in en sert parçası.

**Kabul kriterleri**
- [ ] `GET /schema` bir kez çekiliyor, etiketler/alanlar ondan render ediliyor
- [ ] `adapters.js`'deki yerel etiket tablosu ve `RAW_FIELDS` kaldırılıyor
- [ ] Entity ismi ("öğrenci"/"öğrenciler") arayüz metinlerinde `/schema`'dan
      geliyor
- [ ] Kabul: backend profili "abone"ye çevrildiğinde dashboard kod değişikliği
      olmadan "abone" yazıyor

**Dosyalar:** `src/adapters.js`, `src/components/DetailDrawer.jsx`,
`src/App.jsx`, tüm ekranlar
**Bağımlılık:** B-32
BODY

mk "D-13 · Beyaz etiket: başlık, logo, renkler" "P2,template,area:ui" "backlog" <<'BODY'
**Etiketler:** `P2` `template` `area:ui`

**Kabul kriterleri**
- [ ] Ürün adı / müşteri adı `GET /schema`'daki `display_name`'den
- [ ] Marka rengi ve logo build-time env'den (`VITE_BRAND_*`)
- [ ] `index.html` `<title>` de dinamik
- [ ] EOAI markası ile müşteri markası ayrı tutulabiliyor

**Dosyalar:** `src/theme.js`, `index.html`, `src/components/Header.jsx`,
`Dockerfile`
BODY

mk "D-14 · nginx: `index.html` cache'lenmesin, 404'ler doğru dönsün" "P2,area:ops" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ops`

**Sorun**
`nginx.conf:10-12` `/assets/` için 1 yıl `immutable` veriyor (içerik hash'li
dosya adları için doğru), ama `index.html`'e hiçbir cache header'ı koymuyor —
sadece nginx'in varsayılan ETag/Last-Modified'ı. Araya giren bir proxy ya da CDN
`index.html`'i cache'lerse, yeni deploy sonrası silinmiş hash'li bundle'lara
referans veren eski HTML servis edilir → **beyaz sayfa, hata yok**.

Ayrıca `/assets/` dışındaki her eksik dosya SPA fallback ile 200 + HTML dönüyor;
`/fonts/*.woff2` ve `/favicon.ico` de buna dahil.

**Kabul kriterleri**
- [ ] `location = /index.html { add_header Cache-Control "no-store"; }`
- [ ] `/fonts/`, `/favicon.ico` gerçek 404 dönüyor
- [ ] `index.html`'e favicon ve `<noscript>` ekleniyor

**Dosyalar:** `nginx.conf`, `index.html`
BODY

mk "D-15 · Küçük hatalar ve ölü kod" "P2,area:ui" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ui`

**Sorun** (beşi bir arada, hepsi tek PR)

1. `DetailDrawer.jsx:68` — `/^d+$/` regex'inde ters bölü eksik (byte düzeyinde
    doğrulandı). Literal "d" harfini eşliyor, yani "9. sınıf" dalı ölü. Bugün
    zararsız olmasının tek sebebi `grade` değerlerinin zaten `"12. Sınıf"` /
    `"Mezun"` gelmesi — `/^\d+$/` diye düzeltirsen veriye bakmadan "12. Sınıf.
    sınıf" üretir. Hangi formatın backend'e ait olduğuna karar verip diğer dalı
    sil.
2. `adapters.js:74` — `p: raw.churn_probability` `sayi()`'den geçmeyen tek
    sayısal alan. Null/eksik gelirse `NaN` sessizce yayılıyor: `sort` sırayı
    bozmuyor, `s.p >= esik` false oluyor (öğrenci kayboluyor), `pct()` ekranda
    **"%NaN"** yazıyor.
3. `adapters.js:111` — `churnRiskCount` ölü ve adı yanlış: `threshold=0` ile
    çağrıldığı için `payload.count` toplam öğrenci sayısı (25), risk sayısı değil.
4. `adapters.js:75` — `adaptReasons(raw.top_reasons_detail ?? raw.top_reasons)`
    çift şekli akıllıca yönetiyor, ama `top_reasons_detail` bir gün gelmezse
    string `Array.isArray`'e takılıp `[]` dönüyor ve **her satırın SHAP çipleri
    sessizce kayboluyor**. Bir `else if (typeof detail === 'string') console.warn`
    bunu yüzeye çıkarır.
5. `App.jsx:85-87` — `AbortController` ve cleanup yok. Dedup sayesinde yarış
    yok, ama unmount sonrası ölü ağaca setState oluyor ve yavaş bir SHAP çağrısı
    iptal edilemiyor.
6. `adapters.js:41-48` — `tenureMonths` UTC ile yereli karıştırıyor
    (`new Date("2025-04-10")` UTC gece yarısı, `getDate()` yerel) — UTC'nin
    batısında ay sınırlarında bir ay kayabiliyor. `tenure_months` her zaman
    geldiği için şu an ölü yol.

**Kabul kriterleri**
- [ ] Altısı düzeltiliyor
- [ ] `oxlint` CI'da koşuyor (1. madde tam olarak bir linter kuralının yakalayacağı
      sınıf; şu an `package.json:9`'da komut var ama config dosyası commit'li
      değil ve bu repoda CI yok)

**Dosyalar:** `src/adapters.js`, `src/App.jsx`,
`src/components/DetailDrawer.jsx`, `.oxlintrc.json`, `.github/workflows/`
BODY

mk "D-16 · Bu repoda CI yok" "P2,area:ops" "backlog" <<'BODY'
**Etiketler:** `P2` `area:ops`

**Sorun**
Backend'de `.github/workflows/ci.yml` var, dashboard'da hiç yok. `npm run lint`
ve `npm run build` hiçbir zaman otomatik koşmuyor.

**Kabul kriterleri**
- [ ] `npm ci` + `npm run lint` + `npm run build` her PR'da
- [ ] `docker build` adımı (D-10'daki `VITE_API_BASE` kontrolünü de doğrular)
- [ ] `engines` alanı `package.json`'a (`node >= 22`) — `vite@8` 18'de çalışmıyor
      ve bunu zorlayan bir şey yok

**Dosyalar:** `.github/workflows/ci.yml`, `package.json`
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
