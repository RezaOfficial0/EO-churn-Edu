# EO-Churn — Template Planı

Bu motoru "her müşteri için baştan yazılan bir proje" değil, **config ile yeni bir
dikeye yöneltilen tek bir ürün** haline getirme planı.

**Durum:** `config.FEATURES` gerçekten merkezi, ama onboarding işinin sadece
üçte biri. Entity kimliği, mesaj dili, türetilmiş feature mekanizması, testler ve
dashboard'un etiket tablosu 6+ dosyada sabit. Bu plan onları sökme planı.

---

## 1. Mimari kararı: B

Üç seçenek değerlendirildi:

| | Yaklaşım | Kurulum maliyeti | Bug fix maliyeti | Müşteriye kendi repo'su |
|---|---|---|---|---|
| A | Her müşteri için fork | Sıfır | **N merge** | Evet |
| **B** | **Tek repo, `clients/<slug>/` profilleri** | **Neredeyse sıfır** | **Bir kere** | Hayır (imaj veriliyor) |
| C | Motor pip paketi + ince müşteri reposu | Yüksek (paket, sürüm, indeks) | Bir kere | Evet |

**Karar: B.**

Gerekçe — karmaşıklık matematiği:

- **A elenir.** Fork'un tek avantajı müşteriye kendi repo'sunu verebilmek; bedeli
  motorun N kopyası. Üçüncü müşteride kopyalar ayrışır ve tek bir ürünün değil,
  üç ayrı projen olur. Bug fix N merge demektir.
- **C'nin B'ye tek üstünlüğü** müşterinin kendi repo'suna sahip olması. Ama bu
  ihtiyaç, müşteriye **imaj + kendi config'i** teslim edilerek de karşılanıyor —
  müşteri kodu görmek zorunda değil, kendi sunucusunda çalıştırmak istiyor.
  "Enterprise-owned" iddiası verinin nerede durduğuyla ilgili, repo sahipliğiyle
  değil.
- **B'nin bug fix maliyeti C ile aynı** (bir kere). A'nın sorunu B'de yok.
  Yani C, B'nin çözmediği bir problemi çözmüyor; sadece paket altyapısı
  ekliyor.

**C'ye geçiş tetikleyicisi — şimdi yazıyoruz ki sonra fark edilsin:**

> Bir müşteri **sözleşmeyle kendi git repo'sunu ve kendi CI'ını** şart koştuğunda,
> ya da **dördüncü müşteride**, motor `eoai-churn-core` olarak paketlenir.
> Aşağıdaki refactor paket sınırını zaten üretiyor, dönüşüm mekanik olur.

## 2. Fork almıyoruz — tag atıyoruz

Plan "bu projeye dokunulmayacak, fork alınacak" şeklindeydi. Bunu değiştiriyorum:

**`EO-churn` repo'su motorun kendisi olacak**, refactor bir branch'te yapılıp
testler yeşilken merge edilecek. Bugünkü EO demo'su `clients/edu_demo/`
profiline dönüşecek.

Bugünkü hali "bozulmadan dursun" ihtiyacı gerçek, ama onun aracı fork değil
**tag**:

```bash
git tag -a v1.0-demo -m "Calisan Edu demosu, template refactor oncesi"
git push origin v1.0-demo
```

Tag bedava, ayrışmıyor, ve `git checkout v1.0-demo` ile her an o hale
dönebiliyorsun. Fork ayrışır ve iki yerde bakım ister.

---

## 3. Hedef dizin yapısı

**Bugün**

```
config.py                  <- 300 satir: hem motor ayari hem EO'ya ozel veri
src/data/loader.py         <- SQL'de student_id / enrollment_date sabit
src/notifications/message.py <- Turkce literaller, "ogrenci" kelimesi
db/schema.sql              <- student_id, enrollment_date tipli kolonlar
tests/                     <- EO kolon adlarina bagli
```

**Sonra**

```
config.py                  <- SADECE yukleyici: EOAI_CLIENT'i okur, profili getirir
core/                      <- (src/ yeniden adlandirilabilir) dikey-bagimsiz motor
clients/
  _base.py                 <- ClientProfile sozlesmesi + dogrulama
  edu_demo/
    profile.py             <- bugunku EO ayarlari buraya tasinir
    strings.py             <- Turkce mesaj metinleri, entity ismi
    derive.py              <- monthly_fee -> monthly_value gibi turetimler
    data/                  <- ornek CSV (gitignored, ornek dosya haric)
  _test/
    profile.py             <- testlerin kullandigi minimal sentetik profil
  kunduz/                  <- musteri geldiginde
    profile.py
    strings.py
saved_models/
  edu_demo/                <- profil basina model dizini
    model.cbm
    calibrator.joblib
    model_meta.json
```

Bir müşteri eklemek = `clients/<slug>/` klasörü + `EOAI_CLIENT=<slug>`.

---

## 4. ClientProfile sözleşmesi

`clients/_base.py` bir profilin sağlamak zorunda olduğu şeyi tanımlar. Bugün
`config.py`'de dağınık duran her şey buraya taşınır ve **tek yerde doğrulanır**
(mevcut `_validate_feature_config()` bunun çekirdeği — genişletilecek).

```python
@dataclass(frozen=True)
class ClientProfile:
    slug: str                      # "edu_demo", "kunduz"
    display_name: str              # dashboard basligi, mesaj basligi

    # --- Entity -------------------------------------------------------------
    entity_id_column: str          # musterinin CSV'sindeki ad: "ogrenci_no"
    entity_meta_columns: dict      # {"enrolled_at": "kayit_tarihi", ...}
    entity_noun: tuple[str, str]   # ("ogrenci", "ogrenciler") / ("abone", "aboneler")

    # --- Features -----------------------------------------------------------
    features: list[str]
    cat_cols: list[str]
    target: str
    feature_bounds: dict
    feature_labels: dict           # API /schema ve mesajlar bunu kullanir
    derivations: list[str]         # clients/<slug>/derive.py icindeki isimler
    missing_flag_columns: list[str]
    unimputable_required: list[str]
    impute_group_column: str | None

    # --- Is kurallari -------------------------------------------------------
    decision_cost: dict            # {"false_alarm": 1, "missed_churn": 12}
    capacity_per_run: int          # eski PRECISION_AT_K - mentor kapasitesi
    warning_window_days: int       # kac gun onceden uyari hedefliyoruz

    # --- Model --------------------------------------------------------------
    calibration_method: str
    model_params: dict
    is_synthetic_data: bool        # ARTIK SABIT DEGIL

    # --- Dil / mesaj --------------------------------------------------------
    strings_module: str            # clients/<slug>/strings.py
    locale: str                    # "tr_TR"
```

`config.py` bundan sonra şuna indirgenir:

```python
PROFILE = load_profile(os.environ.get("EOAI_CLIENT", "edu_demo"))
# geriye donuk uyumluluk icin, gecis surecinde:
FEATURES = PROFILE.features
CAT_COLS = PROFILE.cat_cols
...
```

Geriye dönük takma adlar geçiş için kalır, sonra kaldırılır — böylece refactor
tek seferde 40 dosyayı bozmuyor.

---

## 5. T1 — Entity soyutlaması (en kritik karar)

Bugün `student_id` ve `enrollment_date` `loader.py`'daki 7 SQL ifadesinde,
`schema.sql`'de tipli kolon olarak ve `init_db.py`'nin `EXPECTED` dict'inde
sabit.

**Yapmayacağımız çözüm:** SQL'i f-string ile profil adlarından üretmek. Bu
injection yüzeyi açar ve bakımı zorlaştırır.

**Yapacağımız çözüm: veritabanı kolonları generic olur, eşleme sınırda yapılır.**

```
Musterinin CSV'si          Veritabani              API cikti
-----------------          ----------              ---------
ogrenci_no          --->   entity_id        --->   "ogrenci_no"
kayit_tarihi        --->   enrolled_at      --->   "kayit_tarihi"
<24 feature>        --->   features JSONB   --->   "features": {...}
```

- SQL **statik ve generic** kalır — tek satır dinamik SQL yok
- Eşleme iki fonksiyonda toplanır: `to_internal(df, profile)` ve
  `to_external(records, profile)`
- `schema.sql` artık dikey-bağımsız: `entity_id TEXT`, `enrolled_at DATE`
- Migration: `ALTER TABLE ... RENAME COLUMN`. Mevcut veri korunur.

Bu, "template" iddiasını doğru kılan tek en önemli değişiklik.

---

## 6. Adım adım göç planı

Her adım kendi PR'ı, her adımın sonunda `pytest` yeşil ve `demo_up.sh` çalışıyor.

### Adım 0 — Ön koşul: 4 şema değişikliği

Bunlar template'ten **önce** inmeli, yoksa her profilde tekrar edilir.
Detayı `docs/PILOT_PLAYBOOK.md` Faz 1'de (P3, P4, P5):

- `daily_students`'a `as_of_date` — tarihli satırlar. Trend özelliklerinin,
  dashboard'daki kapalı panelin ve modelin yapısal zayıflığının tek çözümü
- `runs` tablosu — koşu kimliği, boş koşu kaydı, `model_version`, `threshold`
- `alerts`'e feature snapshot + `model_version` + `threshold`
- Sonuç (label) ve müdahale kayıt tabloları

**Süre: 4-5 gün. Bu adım atlanamaz.**

### Adım 1 — `clients/` iskeleti + `edu_demo` profili

- `clients/_base.py`: `ClientProfile` + `validate_profile()`
- `clients/edu_demo/profile.py`: bugünkü `config.py` içeriği taşınır
- `config.py` yükleyiciye dönüşür, geriye dönük takma adlar kalır
- **Kabul:** `pytest` yeşil, `demo_up.sh` çalışıyor, `config.py` 300 satırdan
  ~40 satıra düşüyor

### Adım 2 — Testler profilden bağımsızlaşır

- `clients/_test/profile.py`: 6 feature'lı minimal sentetik profil
- `tests/conftest.py` `EOAI_CLIENT=_test` ile koşar
- EO kolon adlarına bağlı assert'ler profilden okunur
- **Kabul:** testler EO verisi olmadan geçiyor. Bugün geçmiyor

### Adım 3 — T1 entity soyutlaması

- `db/migrations/003_generic_entity_columns.sql`: `RENAME COLUMN`
- `loader.py`'da SQL generic kolonlara geçer, `to_internal` / `to_external`
  eklenir
- `init_db.py`'nin `EXPECTED`'i generic olur
- **Kabul:** `entity_id_column = "ogrenci_no"` olan bir profil, kod değişikliği
  olmadan uçtan uca çalışıyor

### Adım 4 — `GET /schema` + dashboard etiketleri API'den

Dashboard'un `adapters.js`'de kendi `FEATURE_LABELS` kopyası var ve kaçınılmaz
olarak ayrışacak. Yeni endpoint:

```json
GET /schema
{
  "display_name": "Kunduz",
  "entity": {"id_field": "ogrenci_no", "noun": "öğrenci", "noun_plural": "öğrenciler"},
  "fields": [
    {"name": "days_since_last_contact", "label": "Son iletişimden bu yana (gün)",
     "type": "number", "unit": "gün", "is_flag": false}
  ],
  "capacity_per_run": 20,
  "warning_window_days": 21
}
```

Dashboard bunu render eder; kendi tablosunu tutmaz. **Dashboard'u tek hamlede
dikey-bağımsız yapan değişiklik bu.**

### Adım 5 — Mesaj sözlüğü ve türetimler

- `clients/<slug>/strings.py`: tüm Türkçe literaller, entity ismi, yön ifadeleri
- `clients/<slug>/derive.py`: `add_monthly_value` gibi türetimler; profil
  hangilerinin uygulanacağını `derivations` listesiyle seçer
- **Kabul:** entity ismini "abone" yapan bir profil, mesajda "8 abone risk
  altında" üretiyor

### Adım 6 — Ufak sertleştirmeler

- `is_synthetic_data` profilden (bugün `True` sabit)
- Model dosya adı → `saved_models/<slug>/model.cbm`
- `single_rule_baseline` default kolonu ve `compare_feature_sets.py`'deki
  `CONTACT_FEATURES` profilden
- Dashboard beyaz etiket: başlık/renk build-time env'den

### Adım 7 — Scaffolder ve onboarding dokümanı

```bash
python scripts/new_client.py kunduz --csv ~/pilots/kunduz/raw/export.csv
```

Yaptığı: `clients/kunduz/` iskeletini kurar, CSV'nin kolonlarını okuyup
`features` taslağını üretir, doldurulacakları `TODO` olarak işaretler, ve
eksikleri listeler. Onboarding'i "dosyaları bul ve düzelt"ten "formu doldur"a
çeviren şey bu.

`docs/ONBOARDING.md`: yeni CSV'den çalışan sisteme kadar komut komut.

### Adım 8 — İkinci dikeyi gerçekten yap

Kanıtlanmamış template iddiası toplantıda çöker. İki aday:

- **OULAD** (32.000 gerçek öğrenci, günlük tıklama akışı, CC BY 4.0) — hem
  template'i kanıtlar hem model hikâyesini sentetikten çıkarır. **Önerilen.**
- Fintech/SaaS sentetiği — daha hızlı ama "gerçek veri" kazanımı yok

### Adım 9 — GitHub template repo'su

Ancak şimdi. `EO-churn` zaten motor; template repo'su `clients/` altında
sadece `_base`, `_test` ve `_skeleton` içeren bir iskelet olur.

---

## 7. Kabul testi

Template'in bittiğinin ölçüsü tek bir cümle:

> **Elimizde bir müşteri CSV'si varken, 2 saat içinde çalışan bir demo
> çıkarabiliyoruz ve bu süre boyunca `core/` altındaki hiçbir dosyaya
> dokunmuyoruz.**

Ölçme yöntemi: Adım 8'i **kronometreyle** yap ve `core/`'a dokunmak zorunda
kaldığın her yeri not et. Her biri template'te kalan bir sızıntı.

---

## 8. Soyutlanmayacak şeyler

Bunları yapmamak bilinçli bir karar; template'i ürün yerine framework'e
çevirmemek için:

- **Model seçimi.** "Müşteri isterse XGBoost" katmanı yok. Bir motor, bir model
  ailesi. Denetim CatBoost'un lojistik regresyonu yenemediğini gösterdi; çözüm
  daha fazla seçenek değil, daha iyi veri
- **Plugin/eklenti sistemi.** Sıfır müşteride
- **Multi-tenancy.** Tek müşteri = tek kurulum = tek veritabanı. İkinci müşteri
  ayrı compose stack'i
- **Dinamik SQL.** Bkz. Adım 3 — generic kolon + sınırda eşleme
- **Çoklu dil altyapısı (i18n framework).** `strings.py` bir dict; gettext
  gerekene kadar gettext yok

---

## 9. Dosya bazında değişiklik özeti

| Dosya | Ne oluyor |
|---|---|
| `config.py` | ~300 satır → ~40 satırlık yükleyici |
| `clients/_base.py` | **yeni** — profil sözleşmesi + doğrulama |
| `clients/edu_demo/*` | **yeni** — bugünkü ayarların yeni evi |
| `clients/_test/*` | **yeni** — testlerin fixture profili |
| `src/data/loader.py` | SQL generic kolonlara, `to_internal`/`to_external` |
| `src/data/features.py` | türetimler kayda, flag/impute listeleri profilden |
| `src/notifications/message.py` | literaller `strings.py`'ye, entity ismi profilden |
| `db/schema.sql` + `003_*.sql` | `student_id`→`entity_id`, `enrollment_date`→`enrolled_at` |
| `scripts/init_db.py` | `EXPECTED` generic |
| `api/main.py` | `GET /schema` eklenir, çıktılar `to_external`'dan geçer |
| `src/model/baseline.py` | default kolon profilden |
| `scripts/compare_feature_sets.py` | `CONTACT_FEATURES` profilden |
| `scripts/new_client.py` | **yeni** — scaffolder |
| `tests/*` | profil fixture'ına bağlanır |
| Dashboard `adapters.js` | kendi etiket tablosunu bırakır, `/schema`'yı kullanır |
| Dashboard `theme.js` | risk bantları `chosenThreshold`'dan türetilir |

---

## 10. Süre

| Adım | Süre |
|---|---|
| 0 — 4 şema değişikliği (ön koşul) | 4-5 gün |
| 1-2 — `clients/` + testler | 2-3 gün |
| 3 — entity soyutlaması | 2 gün |
| 4 — `/schema` + dashboard | 1-2 gün |
| 5-6 — mesajlar, türetimler, ufaklar | 2 gün |
| 7 — scaffolder + doküman | 1 gün |
| 8 — ikinci dikey (OULAD) | 3-5 gün |
| 9 — template repo | 1 saat |

**Toplam ~3 hafta.** Adım 0 ve 8 en uzun ikisi ve ikisi de atlanamaz: biri
şemayı bir kere doğru kurmak, diğeri iddiayı kanıtlamak.
