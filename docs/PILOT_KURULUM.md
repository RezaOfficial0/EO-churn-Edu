# Pilot kurulumu: "evet" diyen bir müşteride sistemi ayağa kaldırmak

[`BACKTEST.md`](BACKTEST.md) geçmişe bakıyordu ve kimse bir şey yapmıyordu.
Burası ileriye bakıyor: model her sabah bir isim listesi üretiyor, bir mentor o
listeyi okuyor ve birini arıyor. Yani bu dosyadan sonra sistemin yanlış davranışı
birinin gerçek sabahına mâl oluyor.

Üç şeye aynı anda cevap veriyor: hangi kararların koddan **önce** verilmesi
gerektiği, nelerin nereye yazıldığı, ve sistemin **henüz yapamadıklarından**
hangilerinin bir pilot için gerçekten önemli olduğu. O son liste en altta ve
atlanamaz: eksikleri saklayan bir pilot kontrol listesi, hiç kontrol listesi
olmamasından kötüdür.

---

## 1. Önce karar, sonra kod

Aşağıdaki yedi karar verilmeden hiçbir komut çalıştırılmaz. Hepsi iş kararı;
sütunlardan biri nereye yazıldığını, biri yanlış verildiğinde ne olduğunu
söylüyor.

| karar | nereye yazılır | yanlışsa ne olur |
|---|---|---|
| **Ayrılma (churn) nedir** | müşterinin cümlesiyle, pilot dokümanına; etiket kolonu olarak eğitim dosyasına | tanımsız etiket, ölçülmeyen bir şeyi ölçmek. `missed_trial_exam_count` ve `payment_delay_days_avg` tanıma göre kısmen etiketin kendisi olabilir — [`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md) bu çifti adıyla işaretliyor |
| **Gözlem penceresi** (ayrılma kaç gün ileride gözleniyor) | `config.CHURN_WINDOW_DAYS` | Bu repoda `30` ve **varsayım**, ölçüm değil. Gerçek bir müşteride müşterinin tanımından gelir; yanlışsa "erken uyarı" penceresi kayar ve geriye-nedensellik kontrolleri anlamsızlaşır |
| **Her satır hangi ana ait** | müşterinin dışa aktarımında `as_of_date`; sistemde **henüz yeri yok** (B-03) | 12 "lagged-needed" kolonun geçmiş penceresi penceresinin içine girip girmediği bilinemez. Audit bu soruyu sentetik veride çözemedi; müşteride çözülebilir ve çözülmesi gerekir |
| **Bir mentor bir koşuda kaç öğrenci arayabilir** | `config.PRECISION_AT_K` | Bu sayı eşik seçimiyle çelişiyorsa ürün çalışmaz. Bölüm 5 bunu ayrı ele alıyor — kurulumun en çok hata üreten yeri |
| **Yanlış alarmın kaçırılan ayrılmaya göre maliyeti** | `config.DECISION_COST` | Eşiği belirleyen şey bu. Varsayılan `{false_alarm: 1, missed_churn: 3}` ve **sınırsız temas kapasitesi varsayıyor**: kaç tane üretilirse üretilsin bir yanlış alarmın maliyeti sabit 1. Gerçek bir mentor ekibinde bu doğru değil |
| **Uyarıyı kim alıyor, hangi kanaldan** | `NOTIFY_CHANNELS` + kanalın kendi değişkenleri | Yanlış kanal = kimse okumuyor. Mentor başına ayrı liste **yok**: tek bir mesaj, tek bir sohbet/adres |
| **Koşu saati ve günleri** | `RUN_AT`, `SCHEDULER_TIMEZONE`, `RUN_DAYS` | Dilim yazılmazsa container saati UTC olduğu için mesaj mentora öğlen ulaşır. `RUN_DAYS=1-5` seçilirse `SCHEDULER_HEARTBEAT_HOURS` ~74'e çekilmeli, yoksa her pazartesi sabahı arıza gibi görünür |

Son üçü dışında hiçbirini kod söylemiyor. İlk dördü müşteriyle oturup
yazılmadan pilot başlamaz.

---

## 2. Nereye ne yazılır

### `config.py` — müşteriye özgü olanlar

README'nin "Onboarding a new client" bölümü bunu 12 madde hâlinde ve dürüstçe
anlatıyor: **"özellik listesi tek bir config düzenlemesi" doğru, "onboarding tek
bir config düzenlemesi" değil.** Burada o listenin pilot için kritik olan kısmı.

Gerçekten sadece config:

| ayar | ne |
|---|---|
| `RAW_DATA_PATH`, `DAILY_DATA_PATH` | eğitim dosyası ve günlük girdinin yolları |
| `FEATURES` | modelin eğitildiği ve servis edildiği **tam** kolon listesi |
| `CAT_COLS`, `CATEGORICAL_LEVELS` | kategorik kolonlar ve kabul edilen seviyeleri. CatBoost görmediği bir kategoriyi reddetmiyor, hash'leyip kendinden emin bir olasılık döndürüyor — bu liste `{"plan_type": "banana"}`'yı reddeden **tek** şey |
| `STUDENT_INFO`, `TARGET_FEATURE` | kimlik kolonları ve etiket |
| `FEATURE_BOUNDS` | sayısal girdilerin kabul aralıkları (`POST /predict` için) |
| `INTEGER_FEATURES`, `FLAG_FEATURES` | hangi sayısal girdiler tam sayı, hangileri 0/1 |
| `FEATURE_LABELS` | mentorun okuduğu Türkçe etiketler |
| `PLAN_MONTHS` | her paketin kaç ayı kapsadığı. Tabloda olmayan bir paket adı uyarıyla 1 ay sayılır, yani fiyat aylık muamelesi görür |
| `CHURN_WINDOW_DAYS`, `CONTACT_FEATURES`, `AUDITED_OUT_FEATURES` | müşterinin kendi sızıntı auditi — mekanizma taşınır, hükümler taşınmaz |
| `DECISION_COST`, `PRECISION_AT_K` | bölüm 1 ve bölüm 5 |
| `MODEL_PATH` | model dosya adı |

`config.py` içe aktarıldığı anda `_validate_feature_config()` çalışıyor ve
`FEATURES`'ı diğer sekiz tabloya karşı kontrol ediyor: tekrar eden ad, bayat
sınır, eksik etiket, özellik listesine sızmış etiket kolonu. **Yeni bir dataset
onboard ederken neyi unuttuğunu bulmanın en hızlı yolu o fonksiyonun hata
mesajlarıdır.** Üyelik kontrol ediyor, **sıra** kontrol etmiyor: `FEATURES`'ı
yeniden eğitmeden yeniden sıralamak CatBoost'un kategorik indekslerini sessizce
kaydırır.

Config olmayan, ama değişmek zorunda olanlar — pilot öncesi bilinmesi gereken
dürüst kapsam:

- `src/data/features.py` — tarif: `UNIMPUTABLE_REQUIRED`,
  `MISSING_FLAG_COLUMNS`, `IMPUTE_GROUP_COLUMN`, ve `add_monthly_value()` (tek
  bir `monthly_fee_try → monthly_value_try` çiftine kaynaklanmış durumda).
- `src/data/loader.py`, `db/schema.sql`, `scripts/init_db.py` — her SQL ifadesi
  `student_id` ve `enrollment_date`'i **sabit** yazıyor. Varlığı `learner_id`
  olan bir müşteri migration gerektirir. `STUDENT_INFO` özellik/kimlik
  ayrımını sürüyor, kolon adlarını sürmüyor.
- `src/notifications/message.py` — mesajın tamamı Türkçe sabit metin
  (`veri yok`, `riski artırıyor`). `FEATURE_LABELS` özellik adlarını kapsıyor,
  etrafındaki cümleleri kapsamıyor.
- `pipeline/training_pipeline.py` — `"is_synthetic_data": True` **sabit**.
  Bırakırsan o müşteriye gösterdiğin her metrik sentetik işaretli gelir.
- `tests/` — `test_loader.py`, `test_notifications.py`, `test_db_integration.py`
  EO kolon adlarına referans veriyor, `conftest.py` `RAW_DATA_PATH`'i yüklüyor.
  **Yeni bir dikeyin ilk gününde test paketi ve dolayısıyla CI kırmızı olur.**
  Bunu pilot gününe bırakma.
- Pano reposu — `src/adapters.js` Türkçe etiketlerin **kendi kopyasını** tutuyor
  (`config.py` ile zamanla ayrışır) ve ~14 adlandırılmış alanı eşliyor;
  `DetailDrawer.jsx`'in kendi `RAW_FIELDS` listesi var. `GET /schema` bu sorunu
  çözmek için var — pano ve kampanya servisi kendi kopyalarını tutmak yerine
  `feature_set_hash`'i sabitleyip tek bir string karşılaştırabilir.

### `.env` ve `.env.docker` — ortam

İkisi ayrı ve ayrı kalmak zorunda: `.env` venv geliştirmesi için `localhost`'a,
`.env.docker` compose ağı içindeki `db` servisine bakıyor. İkisi de sır taşıyor,
ikisi de commit edilmiyor.

Pilot için varsayılandan **değişmek zorunda** olanlar:

| değişken | pilotta ne olmalı |
|---|---|
| `API_KEY` | **gerçek, uzun, rastgele bir değer.** Boşsa API başlamıyor. Bu tek değer iki ucu birden sürüyor: API gerektiriyor, panonun nginx'i enjekte ediyor — yani panoda hiçbir değişiklik gerekmiyor ve anahtar tarayıcıya hiç ulaşmıyor |
| `EOAI_ALLOW_NO_AUTH` | **silinir.** Demo modunda API, kendisine ulaşan her isteği kabul ediyor ve panonun vekili ona compose ağı üzerinden ulaşıyor — o ağa bu yığından başka bir şey katıldığı anda açık bir servis olur |
| `POSTGRES_PASSWORD` | şablondaki `eochurn` bir **demo şifresi**. Kendi dizüstünden başka hiçbir yerde kalmaz |
| `DATA_SOURCE` | `db`. CSV modu geri dönüş yolu olarak kalır ama uyarı geçmişi veritabanında durmalı |
| `NOTIFY_CHANNELS` | müşterinin kanalı. Boş olması "sadece ekrana yaz" demek — ilk hafta için bu doğru olabilir, bölüm 8'e bak |
| `OPS_TELEGRAM_CHAT_ID` ya da `OPS_ALERT_WEBHOOK_URL` | **bizim** kanalımız, müşterinin değil. En az biri **pilot öncesi ayarlanmalı**: hiçbiri yoksa arızayı görünür kılan tek şey container log'u ve `docker compose ps` satırıdır, ve kimse log izlemiyor |
| `RUN_AT`, `SCHEDULER_TIMEZONE`, `RUN_DAYS` | bölüm 1 |
| `SCHEDULER_HEARTBEAT_HOURS` | varsayılan 26. `RUN_DAYS=1-5` ise ~74 |
| `ALLOWED_ORIGINS` | pano aynı origin'den geldiği için sadece başka bir tarayıcı istemcisi varsa önemli |

`API_KEY`'i açmak tek bir düzenleme:

```bash
sed -i '' 's|^API_KEY=.*|API_KEY=<uzun rastgele bir dize>|' .env.docker
sed -i '' '/^EOAI_ALLOW_NO_AUTH=/d'                        .env.docker
./scripts/demo_up.sh
```

---

## 3. Veri yolu: dışa aktarımdan `daily_students`'a

```
müşterinin dışa aktarımı (CSV)
  -> scripts/load_daily_students.py   -> daily_students tablosu
  -> python -m pipeline.daily_pipeline -> puanlama + alerts tablosuna yazma
  -> python scripts/send_daily_alerts.py -> mentora mesaj
```

`load_daily_students.py` dosyayı **önce doğruluyor** (günlük hattın kullandığı
aynı kapı): eksik kolon, fazladan kolon, tekrarlayan `student_id` → hiçbir şey
yazılmadan reddedilir. Öğrenciler `student_id` üzerinden eşleşip yerinde
güncellendiği için aynı dosyayı iki kez yüklemek zararsız.

Pilotta bilmen gereken üç şey:

**Müşteri dosyasını `data/daily_data.csv` üzerine yazma.** O yol
`.dockerignore`'da `data/*` dışlamasından **tek izin verilen** dosya
(`!data/daily_data.csv`). Yani müşteri dışa aktarımını o isimle kaydetmek, onu
o checkout'tan kurulan her imaja sokar — ve imaj bir kez makineden çıktıktan
sonra geri çağrılamaz. Dosyayı başka bir yola koy ve
`load_daily_students.py path/to/export.csv` ile yükle.

**Hiç kimseyi silmiyor.** `daily_students`'ta olup bugünün dosyasında olmayan bir
öğrenci son bilinen değerleriyle kalıyor ve **her gün puanlanmaya devam ediyor**
— çünkü `alerts.student_id` bir yabancı anahtar ve satırı silmek o öğrencinin
uyarı geçmişini de götürür. `--replace` yok, `is_active` bayrağı yok, arşivleme
yolu yok. Programı bitirmiş bir kohort donmuş girdilerle sonsuza kadar uyarı
üretir. Pilotta bu, elle budama demek — ve kimin ne zaman budayacağı yazılı
olmalı.

**Eksik satırlar koşuyu düşürmüyor, sayılıyor.** İki tür eksiklik var ve sadece
biri satıra mâl oluyor (`src/data/features.SERVING_REQUIRED_COLUMNS`):
`weekly_study_hours_actual` ve `satisfaction_survey_score` boşsa `*_missing`
bayrağı eksikliği kaydeder, eğitimde öğrenilen paket-medyanı doldurur, satır
puanlanır. Diğer her ham kolonda ya da `student_id`'de boşluk varsa satır
karantinaya alınır. `QUARANTINE_WARN_RATIO` (%1) üstünde sayı günlük mesaja ve
ops kanalına girer; `MAX_QUARANTINE_RATIO` (%10) üstünde **koşu hata verir** —
hayatta kalan yarısından kurulan bir mesaj, sessiz bir günden ayırt edilemez.

---

## 4. Modeli onların verisiyle eğitmek

**Eğitim kapsayıcı içinde çalışmıyor, kasten.** `data/` imajdan toptan
dışlandığı için `running_train_pipeline.py` kendi girdisini bulamaz. Eğitim bir
iş istasyonunda yapılır, model `saved_models/` ile taşınır. Bunu bir eksiklik
sanıp düzeltmeye kalkma: bir müşteri dışa aktarımının imaja girmemesinin sebebi
bu.

Sıra:

```bash
# iş istasyonunda, repo DIŞINDA bir dizinde duran müşteri dosyasıyla
python running_train_pipeline.py        # eğit, kalibre et, eşik seç, değerlendir
                                        # -> saved_models/ + model_meta.json
```

Eğitimin yaptığı şey (README "How it works"):
doğrulama → özellik mühendisliği (eksik bayrakları, sonra grup-medyan
imputasyonu) → train/val/test bölme → erken durdurmayla CatBoost → Platt
kalibrasyonu → eşik seçimi → tek seferlik test değerlendirmesi.
`model_meta.json` modelle birlikte yolculuk ediyor ve imputasyon medyanlarını ve
seçilen eşiği taşıyor, böylece servis eğitimi birebir tekrarlıyor.

Pilot öncesi düzeltilmesi gerekenler:

1. **`is_synthetic_data`.** `pipeline/training_pipeline.py`'de sabit `True`.
   Düzeltilmezse `model_meta.json` ve `GET /metrics` müşterinin kendi verisiyle
   eğitilmiş modeli sentetik diye işaretler.
2. **Doğrulama kümesi üç iş yapıyor** — erken durdurma, kalibrasyon ve eşik
   seçimi. Sonuç: eşik, modelin uydurulmaya çalıştığı satırlarda seçiliyor ve
   ölçülebilir biçimde iyimser. Aynı eşik 0.29'da: doğrulama maliyeti 409 /
   yakalama 0.576; test maliyeti 469 (+%15) / yakalama 0.473. Çözüm dördüncü bir
   bölme ya da erken durdurma bölmesini train'in içine yuvalamak.
3. **Bölme rastgele, zamansal değil.** Bir erken uyarı ürününde holdout tarihe
   göre olmalı: rapor edilen sayı "bu öğrencilerin kohort arkadaşlarına
   genelliyor mu" değil "gelecek ayın öğrencilerine genelliyor mu" sorusunu
   cevaplamalı.
4. **İmputasyon bölmeden önce öğreniliyor.** `build_training_frame` medyanları
   tüm frame üzerinde öğreniyor, sonra bölme yapılıyor. Sentetik veride (paket
   başına ~1100 satır) etkisi küçük; **küçük bir müşteri pilotunda küçük
   olmayacak.**
5. **Sızıntı auditi yeniden yapılır.** [`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md)'nin
   mekanizması taşınır, hükümleri taşınmaz. Müşterinin kendi kolonları, kendi
   penceresi, kendi `as_of_date`'i ile yeniden yazılır. Özellikle: mentor
   davranışı kolonları (`CONTACT_FEATURES`) ya düşülür ya da müşteri her satır
   için pencere açılışı değerini gönderir ve `CONTACT_LAG_DAYS` açılır —
   mekanizma hazır, eksik kolonda **hata veriyor**, puanlama anındaki değere
   düşmüyor.
6. **Model dosyası sürümsüz.** `MODEL_PATH` sabit bir dosya adı, yani yeniden
   eğitim servis edilen modeli yerinde eziyor ve geri dönüş yok. Pilotta en az
   elle bir kopya al.

### Kabul kuralı

`scripts/compare_feature_sets.py` kuralı kendi söylüyor: **CatBoost lojistik
regresyonu geçmek zorunda, yoksa modelin bir değeri yok.** Sentetik veride
geçmiyor — PR-AUC 0.369'a karşı 0.379, ROC-AUC 0.600'e karşı 0.605. Bir servis
bağımlılığına ve SHAP açıklayıcısına mâl olduğu için bu, gerçek veride
karara bağlanması gereken bir şey. Müşterinin verisinde de geçmezse doğru
hareket modeli savunmak değil, basit modele geçmek ve bunu söylemek.

### Referans sayılar (sentetik veri, `saved_models/model_meta.json`)

Bunlar müşteriye vaat edilen sayılar **değil**; sistemin çalıştığını gösteren ve
yeni bir eğitimin karşılaştırılacağı taban çizgisi.

| | değer |
|---|---|
| eğitim satırı | 3384 |
| özellik | 21 |
| yetiştirilen ağaç | 61 / 300 |
| ROC-AUC (test) | 0.600 |
| PR-AUC | 0.369 |
| Brier (kalibre / ham) | 0.193 / 0.236 |
| CV ROC-AUC | 0.640 ± 0.036 |
| seçilen eşik | 0.29 |
| eşikte kesinlik / yakalama | 0.328 / 0.473 |
| karmaşıklık matrisi (test, n=677) | `[[315, 178], [97, 87]]` |
| `precision@20` / lift@20 | 0.35 / 1.29 |
| `precision@50` (bootstrap %95) | 0.52 [0.38, 0.66], taban oran 0.272 → 1.9× |
| lojistik regresyon taban çizgisi (PR-AUC / ROC) | 0.379 / 0.605 |

Açık Türkçesiyle, sevk edilen çalışma noktasında: **ayrılanların kabaca %47'si
yakalanıyor, uyarıların kabaca %67'si yanlış alarm.** Müşteriye gösterilen her
tabloda bu iki sayı yan yana durmalı; sıralama metriklerini tek başına yazmak
aynı modeli olduğundan iyi göstermenin yoludur.

---

## 5. Eşik ve kapasite: kurulumun en çok hata üreten yeri

İki ayar aynı şeyi iki farklı biçimde söylüyor ve bu repoda **birbiriyle
çelişiyorlar**:

- `DECISION_COST` eşiği seçiyor ve **sınırsız temas kapasitesi varsayıyor** — bir
  yanlış alarm, kaç tane üretilirse üretilsin sabit 1 birim.
- `PRECISION_AT_K = 20` ise bir mentorun bir koşuda yirmi öğrenci arayabildiğini
  söylüyor.

Sonuç: 0.29 eşiğinde model 677 öğrencinin **265'ini** işaretliyor — defterin
%39'u. İkisi birden doğru olamaz. Pratikte mentorlar listenin üstünü
çalışıyor, ki bu da `precision@20`'yi gerçek iş akışını tarif eden sayı yapıyor
— ve o sayı 20 satır üzerinde ölçüldüğü için %95 aralığı [0.15, 0.60].

Pilotta çözülmesi gereken bu. Üç yol var ve seçim senin:

| yol | anlamı | bedeli |
|---|---|---|
| **Eşiği kapasiteye bağla** | her koşuda en riskli K öğrenci gönderilir, eşik sonucu değil girdisi olur | "risk altında" artık bir olasılık eşiği değil bir sıra numarası. Sakin bir gün ile kriz günü aynı sayıda isim üretir |
| **`DECISION_COST`'u kapasiteyle tutarlı hâle getir** | yanlış alarmın maliyeti yükseltilir, eşik yukarı kayar, işaretlenen sayı düşer | maliyet oranı artık bir iş gerçeği değil bir ayar düğmesi olur; gerekçesinin yazılı olması gerekir |
| **Mentor kapasitesini artır** | müşteri tarafında operasyon kararı | bizim elimizde değil, ama konuşulması gereken şey bu olabilir |

Hangisi seçilirse `PRECISION_AT_K` ve `DECISION_COST` **aynı cümleyi söylemek
zorunda**. README bunu şöyle koyuyor: mentor kapasitesinin on katını işaretleyen
bir eşik bir çalışma noktası değil, sıralanmış bir listedir.

Mesaj tarafındaki sınırları da buraya ekle: mesaj en fazla **10 öğrenciyi tam
detayla**, **15 tekrar satırını** taşıyor, gerisi `... ve N öğrenci daha.` olarak
kapanıyor. Telegram'ın 4096 karakter sınırı var ve `send_telegram` aşıldığında
hata vermek yerine **kesiyor** — `SHAP_TOP_N_FEATURES`'ı yükseltmek ya da uzun
`FEATURE_LABELS` göndermek mesajın kuyruğunu sessizce koparabilir.

---

## 6. Kimse güvenmeden önce doğrulama

Sırayla ve hepsi geçmeden mentora tek bir mesaj gitmez.

```bash
pytest                                   # config değişikliği neyi kırdı
python scripts/verify_backend.py         # tüm zincir, hiçbir şey yazmaz
python scripts/verify_backend.py --source db   # veritabanı arka ucunu zorla
python scripts/verify_backend.py --write # gerçek bir günlük koşu da kaydet
```

`verify_backend.py` adım adım pass/fail yazıyor:

```
[1/8] Environment      DATA_SOURCE, DATABASE_URL, auth
[2/8] Model            model, kalibratör, eşik yüklendi mi
[3/8] Data source      bugünün öğrencileri okunuyor, doğrulanıyor
[4/8] CSV/DB parity    (db modu) veritabanı CSV ile aynı şeyi döndürüyor mu
[5/8] Scoring          puanlıyor ve hiçbir şey yazmadığını kanıtlıyor
[6/8] Daily run        (--write) koşu kaydediyor, status değerlerini kontrol ediyor
[7/8] Notifications    mesajı kuruyor, asla göndermiyor
[8/8] API              /health, /students, /metrics, /predict/{id}
```

Çıkış kodu her adım geçtiyse 0, yani bir script içinde duman testi olarak da
kullanılabilir.

Sonra, sırayla:

```bash
# mesajın müşteriye nasıl görüneceği — hiçbir şey göndermez
python scripts/send_daily_alerts.py --dry-run

# imaj hijyeni, KURULU bir imaja karşı: sır yok, öğrenci verisi yok,
# root değil, healthcheck var, taban imaj sabitlenmiş
docker compose --env-file .env.docker build
./scripts/check_image.sh

# zamanlayıcı saati doğru mu — hiçbir şey çalıştırmaz
docker compose --env-file .env.docker exec scheduler python scripts/scheduler.py --next 5

# ops kanalı gerçekten bize ulaşıyor mu
python scripts/telegram_setup.py --test
```

`check_image.sh` atlanmaz: ignore dosyası ne **olması gerektiğini** söylüyor,
sadece imaj ne **olduğunu** söylüyor. Bir müşteri dosyası imaja girdiyse bunu
söyleyen tek şey o script.

Son kontrol kodla ilgili değil: `GET /metrics` çıktısını aç ve
`is_synthetic_data` ile `chosen_threshold` değerlerine bak. İkisi de doğru
değilse panoda ve her metrikte yanlış bir şey gösteriliyor.

---

## 7. Günlük koşu, kanallar, arıza

Günlük koşuyu `docker-compose.yml`'deki **`scheduler`** servisi çalıştırıyor —
elle cron satırı yok. Zincir:

```
python -m pipeline.daily_pipeline  &&  python scripts/send_daily_alerts.py
```

`&&` kasıtlı: koşu başarısız olursa bildirim **hiç başlamıyor**. "Bugün risk
altında öğrenci yok" ile "koşu hiç olmadı" aynı şey değil ve ikincisi bir
öğrenciye mâl olur.

**Kanal ayrımı pilotun en kolay yanlış kurulan parçası.** Müşterinin günlük
mesajı `NOTIFY_CHANNELS`'a gidiyor. **Arızalı bir koşu** tamamen başka bir yere
gidiyor: `OPS_TELEGRAM_CHAT_ID` / `OPS_ALERT_WEBHOOK_URL`, yani bizim kanalımız,
`src/notifications/ops.py` eliyle ve `NOTIFY_CHANNELS` ile hiç ilgisi olmadan.
Bir mentor "KeyError in daily_pipeline" ile bir şey yapamaz, ve bu tam olarak
ürüne olan güvenini bitirecek türden bir mesajdır.

Koşu başarısız olduğunda sırayla: durum dosyasına yazılır → container log'una
büyük harflerle düşer → ops kanalı varsa bize mesaj gider → servis kendini
**unhealthy** ilan eder (`docker compose ps`). Hata metni `redact()`'ten geçiyor:
bot token'ı, webhook adresi ve bağlantı dizesi ne log'a ne mesaja düşüyor.

**Heartbeat**, ürünün doğası gereği görünmez olan tek arızayı yakalıyor:
`SCHEDULER_HEARTBEAT_HOURS` (varsayılan 26) saattir başarılı koşu yoksa ops
kanalına uyarı gidiyor. Sistem üç gündür ölü olsa da, dışarıdan risk altında
kimsenin olmadığı sağlıklı bir gün gibi görünür.

**Uyuyan döngünün dürüst bedeli:** container kapalıyken gelen saat kaçar.
Makine 09:00'da kapalıysa 09:15'te açıldığında koşu yapılmaz; yarının saati
hesaplanır. Yakalayan şey heartbeat'tir. Bunun kasıtlı olduğu yer: keyfî bir
saatte yapılan telafi koşusu `new` / `still_at_risk` karşılaştırmasını o saatte
yeniden tanımlardı.

`send_daily_alerts.py` iki durumda göndermeyi **reddediyor**: uyarı kaydında hiç
koşu yoksa, ve son koşu `--max-age-hours`'tan (varsayılan 24) eskiyse. İkisi de
1 ile çıkıyor; `--force` ikisini de geçersiz kılıyor.

**Demo scriptleri müşteri kurulumuna yaklaştırılmaz.** `demo_reset.sh` `alerts`
tablosunu boşaltıyor ve `seed_demo_history.py` sentetik bir "dün" koşusu
uyduruyor. İkincisi mevcut koşuları olan bir uyarı kaydına `--force` olmadan
çalışmayı reddediyor — ama bu bir güvenlik önlemi değil bir frenden ibarettir.
Bir müşteri makinesinde bu iki komut hiç çalıştırılmaz.

---

## 8. İlk iki hafta

Fikir basit: sistem mentora ulaşmadan önce bir hafta boyunca **bize** ulaşsın.

### Hafta 1 — gölge modu

`NOTIFY_CHANNELS` müşterinin kanalına **değil**, bizim kanalımıza ayarlı (ya da
boş: boş olması "sadece ekrana yaz" demek). Model her sabah koşuyor, uyarı kaydı
birikiyor, mesaj bize geliyor.

Her sabah bakılacaklar:

- [ ] koşu oldu mu — `docker compose --env-file .env.docker logs --since 24h scheduler`
- [ ] kaç öğrenci işaretlendi; bu sayı mentor kapasitesiyle aynı mertebede mi
- [ ] karantina sayısı kaç, hangi kolonlardan
- [ ] `new` / `still_at_risk` dağılımı mantıklı mı — her sabah herkesin `new`
      görünmesi, bir önceki koşunun kaydedilmediğini gösterir
- [ ] SHAP gerekçeleri öğrenciye göre değişiyor mu, yoksa her öğrencide aynı iki
      kolon mu çıkıyor. Aynı iki kolon çıkıyorsa mesaj bilgi taşımıyor — audit
      öncesi tam olarak bu oluyordu
- [ ] olasılıklar bir yere yığılmış mı (kalibrasyon kokusu)

Hafta sonunda müşteriyle bir oturum: işaretlenen isimleri mentor okur ve "bunlar
senin de endişelendiğin isimler mi" sorusuna cevap verir. Bu, hiçbir metriğin
veremeyeceği bir sinyal: liste mentorun kendi sezgisiyle hiç örtüşmüyorsa ya
model yanlış ya etiket tanımı yanlış, ve bunu ikinci haftada öğrenmek daha iyi.

### Hafta 2 — canlı

`NOTIFY_CHANNELS` müşterinin kanalına çevrilir. Günlük kontroller aynı, iki ek:

- [ ] mesaj gerçekten ulaştı mı (sadece gönderildi mi değil)
- [ ] mentor ne yaptı — kaç isim arandı. Sistem bunu **kaydetmiyor**, elle
      sorulur. Bölüm 9'daki eksiklerden biri bu

Ve bir hatırlatma: `alerts` tablosu sistemin ne yaptığının **tek** kaydı ve
hiçbir şey onu düzenli olarak yedeklemiyor. İlk canlı günden itibaren:

```bash
docker compose --env-file .env.docker exec -T db \
  pg_dump -U postgres -d eo_churn > backup-$(date +%F).sql
```

`daily_students` CSV'den yeniden kurulabilir; **`alerts` hiçbir şeyden yeniden
kurulamaz.** Her `down -v` o geçmişi siler.

---

## 9. Sistemin henüz yapmadıkları

Bunları saklayan bir pilot kontrol listesi işe yaramaz: pilot sırasında bir
müşteri sorusu olarak ortaya çıkarlar ve o anda cevap vermek, baştan söylemekten
çok daha pahalıdır. Hepsi README "Known limitations"ta ve kodun içinde kendi
numarasıyla yazılı.

> `docs/ISSUE_ORDER.md` bu repoda **yok** (bak: bu dosyanın sonundaki not).
> Aşağıdaki liste README "Known limitations" bölümü ve kodda geçen `B-xx`
> işaretlerinden derlendi. Açık iş sırasını gösteren bir dosya varsa bu bölüm
> ona göre düzeltilmeli.

### Pilotu gerçekten etkileyenler

**1. Koşu kaydı yok (B-04), ve boş bir koşu hiç iz bırakmıyor.**
Bir koşu yalnızca Python tarafında üretilmiş bir mikrosaniye zaman damgasıyla
tanımlı; `runs` tablosu yok. Zamanlayıcının durum dosyası **son** koşunun
başarılı mı başarısız mı olduğunu tutuyor — bir satır, bir geçmiş değil. Koşu
süresi yok, kimseyi bulamamış bir koşunun kaydı yok. Kimse risk altında
olmadığında hiçbir şey yazılmıyor, yani **"hat koştu ve herkes iyi" ile "hat üç
gündür ölü" birbirinden ayırt edilemiyor** — ve sonraki koşu bayat bir temelle
karşılaştırma yapıyor. Bir uyarı ürününde sessizlik belirsiz olmamalı.
*Pilotta ne yapılır:* heartbeat ve `--healthcheck` bunu gürültülü hâle getiriyor,
ama yerine geçmiyor. Ops kanalı mutlaka ayarlı olmalı ve ilk iki hafta boyunca
koşu log'u elle kontrol edilmeli.

**2. Uyarı satırlarında özellik anlık görüntüsü, model sürümü ve eşik yok.**
`score_students` puanladığı tam özellik değerlerini hesaplıyor, **yazma yolu
onları düşürüyor**. Hangi model ve hangi eşik bir uyarıyı ürettiği kayıtlı
değil, ve eşik maliyet türevi olduğu için her yeniden eğitimde kendi başına
kayıyor. İki sonucu var ve ikincisi ağır:
- "Bu öğrenciyi 3 Eylül'de neden işaretledin" sorusunun cevabı yok.
- **Ürünün değer iddiasının dayandığı sonuç analizi — "işaretlediklerimizin kaçı
  gerçekten ayrıldı" — koşturulamıyor.** Yani bir pilotun en ikna edici başarı
  kriteri, bugünün sistemiyle ölçülemez.
*Pilotta ne yapılır:* sonuç kriteri seçilecekse bu eksik önce kapatılmalı. Geçici
çözüm, her koşuda `alerts`'in bir kopyasını ve o günün girdi dosyasını ayrı
saklamak — elle, ve bunu kim yapacak yazılı olmalı.

**3. Sonuç tablosu yok.** Yukarıdakinin diğer yarısı: hangi öğrencinin sonunda
ne olduğunu tutan bir yer yok. Ayrılma gerçekleştiğinde onu uyarı geçmişiyle
eşleyen hiçbir şema parçası bulunmuyor. Pilotun ölçümü bu nedenle kısmen elle
yapılacak; bunun kimin işi olduğu baştan belli olmalı.

**4. Yedek yok.** Otomatik hiçbir şey `alerts`'i dökmüyor. Bölüm 8'deki
`pg_dump` komutu elle. `down -v` veritabanı volume'ünü siliyor, yani tüm uyarı
geçmişini.

**5. KVKK/silme yolu yok.** `alerts.student_id` `ON DELETE` politikası olmayan
bir yabancı anahtar ve `daily_students`'tan hiçbir şey silmiyor — yani bir silme
talebi ancak o öğrencinin uyarı geçmişi yok edilerek karşılanabiliyor. Saklama
süresi yok, anonimleştirme yordamı yok. Veri konusu çoğunlukla **reşit olmayan**
öğrenciler olduğu için bu, pilot sözleşmesinde konuşulması gereken bir madde,
bir teknik borç kalemi değil.

**6. `as_of_date` yok (B-03).** `daily_students` bir satırın hangi ana ait
olduğunu tutmuyor. "Lagged-needed" 12 kolonun geçmiş penceresinin, tahmin
edilmesi gereken pencereyle çakışıp çakışmadığını söyleyecek tek şey bu.

**7. `daily_students` hiç budanmıyor.** Bölüm 3. Uzun süren bir kurulumda elle
budama gerekiyor.

**8. Kimlik doğrulama ya hep ya hiç.** Tek bir paylaşılan sır: kişi bazlı kimlik
yok, rotasyon yok, **kimin hangi öğrenciye baktığının denetim kaydı yok.** Adı
belli mentorlarla yapılan bir pilotta bu, bir detay değil sıradaki iş.

**9. Öğrenci kimlikleri log'lara ve bazı hata gövdelerine ulaşıyor.** Erişim
log'u rota şablonunu yazıyor (`/predict/{student_id}`), ama doğrulama hataları
gerçek id'leri ve iç kolon listesini `detail` alanında döndürüyor.
`docs/LOGGING.md` neyin yazıldığını ve 30 günlük saklama süresini anlatıyor —
ama **repodaki hiçbir şey o 30 günü zorlamıyor**; Docker'da bir `logging`
sürücüsü ayarı, systemd'de bir journald ayarı olarak kurulması gerekiyor.

**10. Hiçbir yere kurulmuş değil, ve repoda TLS sonlandıran, hız sınırlayan ya
da istek boyutunu sınırlayan hiçbir şey yok.** Bunlar verilmemiş bir ters vekil
kararı. Müşteri sunucusuna çıkmadan önce verilmesi gerekiyor.

### Daha az yakıcı olanlar, ama bilinmesi gerekenler

- **`alerts`'i okuyan bir endpoint yok.** Geçmiş ve `status` kaydedilmiş ama
  API'den sunulmuyor, yani pano bunları gösteremiyor.
- **`GET /students` her çağrıda her şeyi yeniden puanlıyor**, sayfalama ve limit
  yok. Bu ölçekte sorun değil (25 öğrenci için ~70 ms), on bin öğrencide yavaş,
  ve büyük bir listede tek bir `?threshold=0` worker havuzunu container
  healthcheck'i düşecek kadar meşgul edebilir.
- **CSV modu `top_reasons_detail`'i tutamıyor** — mesaj katmanı gösterim
  dizesini yeniden ayrıştırıyor. Veritabanı modunda `(run_at, student_id)`
  üzerinde tekil indeks var; CSV modu her ekleme çevresinde bir `flock`'a
  güveniyor, yani gerçek bir yinelemeyi sadece Postgres reddediyor.
- **Telegram mesajı 4096 karakterde kesiliyor**, hata vermiyor.
- **Pano kendi Türkçe etiket kopyasını tutuyor** ve `config.py` ile ayrışacak.
  `GET /schema` + `feature_set_hash` bunun çözümü ve pano tarafında
  kullanılması gerekiyor.
- **`RnD/` ürünün parçası değil.** CI'dan dışlanmış, hiçbir şey onu içe
  aktarmıyor, `model_2.py` şu anda hiç import edilemiyor ve `model_3.py` zaten
  mühendisliği yapılmış bir frame'e özellik mühendisliğini tekrar uyguluyor —
  `metrics/` altındaki ANN-CatBoost sayıları farklı özelliklerle hesaplandı ve
  **alıntılanmamalı**.

---

## 10. Senin kararların

| karar | seçenekler ve her birinin anlamı |
|---|---|
| **Pilot ne kadar sürer** | Pencere uzunluğunun katı olmalı: `CHURN_WINDOW_DAYS` 30 gün ise 30 günlük pilot tek bir pencere görür ve hiçbir sonucu ölçemez. En az iki-üç pencere (2–3 ay). Daha kısası sadece "sistem ayakta kaldı mı" sorusunu cevaplar — bu da bir cevaptır ama başarı kriteri olarak satılamaz |
| **Başarı kriteri ne** | *Model metriği* (ilk K'da isabet ≥ X): ölçmesi kolay, ikna etmesi zor. *Operasyonel* (mentorlar listeyi her gün kullandı, şu kadar temas yapıldı): ürünün kullanıldığını gösterir, modeli göstermez — **ve bugün ölçülebilir olan tek şey bu**. *Sonuç* (işaretlenenlerde ayrılma, işaretlenmeyenlere göre düştü): tek gerçek ikna edici kriter, ama bölüm 9/madde 2 ve 3 kapatılmadan **ölçülemez**. Seçim, hangi eksiği kapatacağını da belirliyor |
| **Fiyat** | Pilot ücretli mi, ücretsiz mi, yoksa sonraki sözleşmeden düşülecek mi. Ücretsiz bir pilotun müşteriye hiçbir taahhüdü yoktur ve terk edilmesi bedavadır; ücretli bir pilot satın alma süreci açar ve ciddiye alınır ama bazı adayları baştan düşürür |
| **Veriye pilot sonunda ne olur** | Saklama süresi, silme yordamı, kimde durduğu. Bölüm 9/madde 5 nedeniyle bu **teknik olarak kolay değil**: öğrenci silmek uyarı geçmişini de siliyor. Sözleşmeye yazılacak cümle bu kısıtla uyumlu olmalı — yazamayacağın bir sözü vermemek için önce o eksiği kapatmak da bir seçenek |
| **Kaç mentor, kaç öğrenci** | Tek bir mesaj, tek bir kanal var: mentor başına ayrı liste **yok**. Birden fazla mentor demek, ya herkesin tüm listeyi görmesi ya da bizim tarafta yeni iş |
| **Eşik mi kapasite mi** | Bölüm 5'teki üç yol |
| **Pano pilotta var mı** | Varsa: `GET /schema` kullanımı, etiket kopyası ve kimin hangi öğrenciye baktığının kaydının olmaması konuşulmalı. Yoksa: ürün Telegram/e-posta mesajından ibaret, ki bu bir pilot için yeterli olabilir ve kurulumu çok daha küçük |
| **Hangi eksikler pilottan önce kapatılır** | Bölüm 9'daki ilk beş madde. Hepsini kapatmak pilotu geciktirir; hiçbirini kapatmamak pilotun kendi sonucunu ölçememesi demek. En az 1 (koşu kaydı) ve 2 (özellik anlık görüntüsü) ile ilgili bir karar verilmeli |

---

## 11. Komut referansı

> Bu bölüm dışındaki her şey bayraklardan bağımsızdır. Aşağıdakiler README
> "Command reference" ile aynı ve repoda doğrulanmıştır — **tek istisna**
> `scripts/backtest.py` ve `scripts/make_synthetic_history.py`: bu dosya
> yazılırken paralel olarak geliştiriliyorlardı ve bayrakları doğrulanmadı. Onlar
> [`BACKTEST.md`](BACKTEST.md) içindeki işaretli bölümde duruyor, burada hiç
> geçmiyor.

### Kurulum

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt     # testler için
cp .env.example .env                    # ya da cp .env.docker.example .env.docker
```

### Eğitim (sadece iş istasyonunda — kapsayıcı içinde çalışmaz)

```bash
python running_train_pipeline.py        # eğit, kalibre et, eşik seç, kaydet
python scripts/build_training_data.py   # mühendisliği yapılmış frame'i incelemek için
python scripts/compare_feature_sets.py  # özellik kümesi karşılaştırması + bootstrap
```

### Veritabanı (`DATA_SOURCE=db`)

```bash
python scripts/init_db.py                          # bekleyen migration'lar, sonra doğrula
python scripts/init_db.py --verify                 # sadece kontrol
python scripts/init_db.py --create-db              # veritabanını da oluştur
python scripts/load_daily_students.py path/to/export.csv
```

### Günlük koşu

```bash
python -m pipeline.daily_pipeline                  # puanla ve kaydet
python scripts/send_daily_alerts.py                # bildir
python scripts/send_daily_alerts.py --dry-run      # mesajı yaz, gönderme
python scripts/scheduler.py --next 5               # sonraki 5 koşu anı
python scripts/scheduler.py --once                 # zinciri şimdi koştur
python scripts/scheduler.py --healthcheck          # son koşu eski/başarısızsa 1
```

### Doğrulama

```bash
pytest
python scripts/verify_backend.py
python scripts/verify_backend.py --source db
python scripts/verify_backend.py --write
./scripts/check_image.sh
```

### Docker

```bash
docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f scheduler
docker compose --env-file .env.docker down         # durdur, veriyi koru
docker compose --env-file .env.docker down -v      # durdur ve VERİTABANINI SİL
```

### Yedek

```bash
docker compose --env-file .env.docker exec -T db \
  pg_dump -U postgres -d eo_churn > backup-$(date +%F).sql

docker compose --env-file .env.docker exec -T db \
  psql -U postgres -d eo_churn < backup-2026-09-19.sql
```

### Müşteri kurulumunda ÇALIŞTIRILMAYACAK komutlar

```bash
./scripts/demo_reset.sh              # alerts tablosunu boşaltır
python scripts/seed_demo_history.py  # sentetik "dün" koşusu uydurur
```

---

## Bu dosyanın bilinen eksiği

`docs/ISSUE_ORDER.md` bu repoda bulunmuyor. Bölüm 9'daki eksik listesi README
"Known limitations" bölümünden ve kodda geçen `B-xx` / `D-xx` işaretlerinden
(`B-03` → `as_of_date`, `B-04` → `runs` tablosu, `B-28` → karantina, `D-09` →
pano vekili) derlendi. Açık işlerin sırasını veren bir dosya varsa bölüm 9'un
sıralaması ona göre yeniden düzenlenmeli — hangi eksiğin pilottan önce
kapatılacağı kararı (bölüm 10'un son satırı) o sıraya bağlı.
