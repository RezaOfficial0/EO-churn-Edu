# Geriye dönük test: iç işleyiş yordamı

Bir yatırımcı ürün demosunu izlemeyi reddetti. Gerekçe ürünle ilgili değildi:
gerçek bir kullanım yok, gerçek müşteri verisi yok, dolayısıyla geri kalanı
önemsiz. Buna repo içinden verilecek bir cevap yok, ve "önce bize entegre olun"
diye başlayan bir cevap da yok.

Teklif bu yüzden ters yönde kuruldu: aday kurum son 12 ayın anonimleştirilmiş
dışa aktarımını gönderir, iki hafta sonra kaybettiği öğrencilerden hangilerinin
işaretlenebileceğini, kaç gün önce işaretlenebileceğini ve hangilerinin
kaçırılacağını gösteren bir rapor alır. Entegrasyon yok, kurulum yok,
sistemlerine erişim yok.

Bu dosya o hareketin **iç yordamı**: aday kabul ettiği andan rapor teslimine
kadar ne yapılacağı. Üç kardeş dosya var ve hiçbiriyle çakışmaz:

| dosya | ne |
|---|---|
| [`BACKTEST.md`](BACKTEST.md) | **teknik tasarım**: walk-forward şeması, embargo, kodun nerede garanti verdiği. İngilizce. Burada tekrarlanmıyor — mekanizmayı anlamak gerektiğinde oraya bakılır |
| [`VERI_TALEBI.md`](VERI_TALEBI.md) | aday kuruma **gönderilen** veri talebi |
| bu dosya | aday kabul ettikten sonra bizim yaptıklarımız |

Girdi sözleşmesinin **tek yetkili kaynağı** bu dosya değil,
`python scripts/backtest.py --contract` çıktısıdır: scriptin doğrulama yaptığı
sabitlerden basılıyor, yani prosa bir kopyanın aksine koddan ayrışamıyor. Aşağıda
sözleşmeye dair ne yazılıysa, çakışma hâlinde `--contract` doğrudur.

---

## Bu testin söylediği ve söylemediği şey

Tek cümleyle: **backtest değerin görünür olduğunu gösterir, kurtarıldığını
göstermez.**

Geçmişte kimse müdahale etmedi, hiçbir öğrenci aranmadı. İşaretlenen
öğrencilerin ayrılmış olması, sistem o zaman kurulu olsaydı onların kalacağı
anlamına gelmez — mentorun araması işe yarar mıydı, bu testin cevap
veremeyeceği ayrı bir sorudur. Elde edilen şey şu: *ayrılmanın, ayrılmadan önce
verinin içinde görünür olduğu*.

Bu ayrımı bir kez yanlış kurmak, hareketin tamamının dayandığı güvenilirliği
bitirir. Aday kurumun kendi verisiyle yapılmış bir analizde "şu kadar öğrenci
kurtarılırdı" cümlesini bir kez söylersen, geri kalan her sayı da pazarlama
sayılır — ve demoyu izlemeyi reddeden adamın reddettiği şey tam olarak bu.

Script bu kuralı kendi içine gömmüş durumda: parasal bölüm JSON'a
`value_at_risk_not_value_saved` adlı bir alanla yazılıyor ve "kurtarılan gelir
olarak okunabilecek" bir anahtar dosyada **yok**. Yasak cümlelerin tam listesi
aşağıda: [Sunum](#sunum-ne-söylenir-ne-söylenmez).

| | backtest cevaplar | backtest cevaplamaz |
|---|---|---|
| sıralama | ayrılanlar listenin üstüne çıkıyor mu | müdahale edilseydi ne olurdu |
| zamanlama | ayrılmadan kaç gün önce işaretlenirdi | o süre müdahale için yeterli miydi |
| iş yükü | mentorun kapasitesi kadar isim verince kaçı gerçekten ayrılan | mentorun o isimlerle ne yapacağı |
| kaçırma | kimleri kaçırdık, hangi segmentte | onları kaçırmanın bedeli |
| veri | sinyal onların kendi verisinde var mı | sinyal gelecek yıl da orada olacak mı |

İkinci kolon pilotun işi. Backtest pilota girmeye değip değmediğini söyleyen
şeydir, pilotun sonucunu söyleyen şey değildir.

---

## Verilmiş kararlar

Bunlar artık açık soru değil. Yordamın parçası olarak uygulanır, aday kurumla
yeniden müzakere edilmez.

Bunlar [`VERI_TALEBI.md`](VERI_TALEBI.md)'nin "Veriye ne oluyor", "Ücret" ve
"Sözleşme" bölümlerinde **aday kuruma söz olarak verilmiş** durumda. Buradaki
tablo o sözün bizim tarafımızdaki karşılığı; ikisi ayrışırsa yanlış olan taraf
bizim yordamımızdır, söz değil.

| karar | ne |
|---|---|
| **Ücret** | Backtest **ücretsizdir.** Teklif edilirken fiyat konuşulmaz, "pilot ücretinden düşülecek" denmez |
| **Veri** | **Rapor teslim edildiği gün silinir.** Pilot kararı beklenmez, "belki sonra işimize yarar" diye tutulmaz |
| **NDA** | Aday kurum **isterse** imzalanır — kendi metnini gönderir, biz imzalarız. **Kendi taslağımızı asla dayatmıyoruz** ve bu konuyu biz gündeme getirmiyoruz |

Verilen sözün tam metni dört madde ve her biri bizim tarafta bir iş:

> Dosyalar elimizde olduğu sürece: **tek bir bilgisayarda, şifreli diskte**;
> hiçbir **bulut** hizmetine, hiçbir **yedeklemeye**, hiçbir **kod deposuna**
> girmiyor; analizi yapan kişi dışında kimseye açılmıyor; **hiçbir model
> eğitiminde** ve hiçbir başka müşteri çalışmasında kullanılmıyor. Rapor da
> **öğrenci bazlı satır içermiyor.**

Somut yordam: [Veri hijyeni ve teslimde silme](#veri-hijyeni-ve-teslimde-silme).
O bölüm aynı zamanda bu sözün **hangi kısmının gerçekten tutulabildiğini**
tartışıyor — çünkü bir sözü vermenin doğru zamanı, tutulamayacağını fark etmeden
öncesidir.

Silme kararının bir sonucu var ve baştan kabul edilmiş olması gerekiyor: **pilot
modeli aynı veriyle eğitilemez.** Pilot başlarsa veri yeniden istenir. Bu,
teslimde net bir söz verebilmenin bedeli ve bilinçli olarak ödeniyor.

---

## Takvim

İki hafta söz veriliyor. Gerçekte iki haftanın büyük kısmı beklemeyle geçiyor;
kritik olan veri geldiği gün.

| gün | ne olur |
|---|---|
| 0 | aday kabul eder, `VERI_TALEBI.md` gider |
| 0–7 | veri gelir. Tecrübeyle burada iki hafta da geçebilir — **süre sözü veri geldikten sonrası için verilir**, talebin gönderildiği gün için değil |
| veri geldiği gün | [uygunluk kontrolü](#uygunluk-kontrolü-veri-geldiği-gün). 30–60 dakika, kod yazılmaz. Engel varsa **o gün** söylenir |
| +1 … +3 | eşleme JSON'u, etiket tanımının yazıya geçmesi, ilk koşu |
| +4 … +7 | parametre duyarlılığı (`--step-days`, `--k`, `--active-window-days`), atlanan fold'ların nedenleri |
| +8 … +10 | rapor yazımı, sayıların ikinci kez doğrulanması |
| teslim | rapor + 30 dakikalık okuma görüşmesi + **veri silinir** |

Uygunluk kontrolü bir formalite değil, yordamın en değerli adımı: iki haftayı
kullanılamaz bir dosyaya harcamanın tek panzehiri o.

---

## Önce senin karar vermen gerekenler

Veri istenmeden önce yazılı olması gereken, aday kuruma sorulacak üç şey var.
Teknik değil tanım sorularıdır ve cevapsız bir backtest ölçtüğü şeyi bilmez.

1. **Ayrılma (churn) nedir?** "Ödemeyi kesti", "programı bıraktı", "iki ay giriş
   yapmadı", "sözleşmeyi yenilemedi" — dördü farklı sayılar üretir. Kurumun
   kendi tanımı kullanılır; bizimki dayatılmaz.
2. **Pencere ne kadar?** Ayrılma hangi uzunluktaki ileriye dönük pencerede
   gözleniyor. `--churn-window-days`, varsayılanı `config.CHURN_WINDOW_DAYS` =
   30 ve bu bir **varsayım**, ölçüm değil ([`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md)).
   Müşterinin tanımı neyse bayrakla verilir.
3. **Bir mentor bir dönemde kaç öğrenci arayabiliyor?** `--k`, varsayılanı
   `config.PRECISION_AT_K` = 20. Hem isabet oranının hem de kapsama tavanının
   tamamı bu sayıya bağlı; kurumun gerçek sayısı alınmadan koşulan bir backtest
   başka bir kurumun sorusunu cevaplar.

Dördüncüsü teknik ama aynı derecede önemli: **her satırın feature değerleri o
satırın `as_of_date`'inde geçerli olan değerler olmak zorunda.** Bunu dosyadan
doğrulayamıyoruz — müşterinin beyanıdır ve script onu raporun içine bir uyarı
olarak koyuyor. Yanlışsa bütün sayılar yukarı sapar.

---

## Uygunluk kontrolü (veri geldiği gün)

Tek bir karar: **bu dosyayla iki hafta harcanır mı?**

Önemli bir değişiklik: zorunlu kolon listesi artık **iki kolon**. Her zorunlu
kolon adayın vazgeçmesi için bir sebep, ve vazgeçen bir aday yirmi feature
yerine dokuz feature'la koşulmuş bir backtest'ten daha az değerli. Script bu
yüzden neredeyse her şeyi opsiyonel kabul ediyor.

### Gerçekten zorunlu olan

| dosya | zorunlu | ne |
|---|---|---|
| geçmiş (`--history`) | `student_id` | istikrarlı, anonim kimlik. Herhangi bir dize |
| | `as_of_date` | satırın tarif ettiği tarih |
| sonuçlar (`--outcomes`) | `student_id` | geçmiş dosyasındaki **aynı** kimlikler |
| | `churn_date` | ayrıldığı tarih. Ayrılmayan için boş |
| eşleme (`--mapping`) | — | müşterinin kolon adları → bizimkiler. Adlar zaten bizimkiyle aynıysa bölüm hiç yazılmaz |

Sonuçlar dosyasında olmayan öğrenciler **hiç ayrılmamış** sayılıyor. Opsiyonel
bir `churned` bayrağı varsa, "true" olan her satırda `churn_date` dolu olmak
zorunda.

### Scriptin gerçekten reddettiği şeyler

Bunlar tahmin değil, `BacktestInputError` ile koşuyu durduran durumlar:

| kontrol | eşik | nerede |
|---|---|---|
| feature kolonu sayısı | **3'ten az** → durur | `config.BACKTEST_MIN_FEATURES` |
| geçmişin kapsadığı gün | **90 günden az** → tek bir değerlendirme noktası bile kurulamaz | `warmup_days` (varsayılan W + S = 60) + `churn_window_days` (30) |
| sonuçlar dosyasının kapsadığı öğrenci | geçmişteki öğrencilerin **%50'sinden az** → durur, `--allow-partial-outcomes` ile geçilir | `config.BACKTEST_MIN_OUTCOME_COVERAGE` = 0.5 |
| fold başına eğitim satırı | **200'den az** → o fold atlanır | `config.BACKTEST_MIN_TRAIN_ROWS` |
| fold başına eğitim ayrılması | **10'dan az** → o fold atlanır | `config.BACKTEST_MIN_TRAIN_CHURNERS` |
| kullanılabilir fold | **hiç yoksa** → durur, her noktanın atlanma nedeniyle | `run_backtest()` |
| zamansal sızıntı | herhangi bir satır geleceği görüyorsa → `TemporalLeakageError`, koşu ölür | `_assert_only_past()` |

**90 gün kuralı en çok karşılaşılacak ret sebebi ve aritmetik:** bir
değerlendirme noktasının arkasında 60 gün geçmiş, önünde 30 gün gözlenmiş sonuç
olmak zorunda. Yani **tek bir anlık görüntü (snapshot) ile backtest
koşulamıyor** — script açıkça reddediyor ve "daha uzun bir export gönderin, ya da
`--churn-window-days` / `--step-days`'i düşürün" diyor.

> ⚠️ `VERI_TALEBI.md`'nin "En az hâli" tablosunda hâlâ *"sadece tek bir güncel
> kesit + ayrılma kayıtları → sıralama testi yapabiliriz"* diyen bir satır var.
> **Bu artık doğru değil.** O satır düzeltilmeli; bkz. bu dosyanın sonundaki
> [Doğrulanamayanlar](#doğrulanamayanlar-ve-çakışmalar).

### Durduran şeyler (kırmızı çizgi)

| kontrol | neden durdurur |
|---|---|
| ayrılma kayıtları yok ve geçmişte bir ayrılma bayrağı da yok | ölçülecek bir şey yok |
| etiket tanımı yazılı değil | hangi olayın ölçüldüğü bilinmiyorsa her sayı yorumsuz |
| geçmiş 90 günden kısa, ya da tek kesit | script reddediyor. Yukarıya bak |
| üçten az feature kolonu | script reddediyor. İki feature'lık bir model grafik üretir ve bu, üç kolon daha isteyen bir e-postadan **kötü** bir sonuçtur |
| dosyada kişisel veri var (isim, telefon, e-posta, TC, veli adı, adres, doğum tarihi) | işlemeye girmeden geri gönderilir. Script bunu kontrol **etmiyor** — bu bir insan adımı ve atlanamaz |
| ayrılma etiketi, feature kolonlarından birinin kendisi | ör. etiket "ödemeyi kesti" iken `payment_delay_days_avg` gönderilmiş. [`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md) bu çifti adıyla işaretliyor: `missed_trial_exam_count` ve `payment_delay_days_avg`. Çıkarılır; çıkarılamıyorsa durulur |

### Durdurmayan, sadece kapsamı daraltan şeyler

| durum | sonuç |
|---|---|
| 19 feature'ın yarısı yok | kalanla koşulur. Script gelmeyen her kolonu `features_not_supplied` altında **rapora yazıyor** |
| mentor davranışı kolonları gelmiş | script onları tanıyor, **kullanmıyor**, `audited_out_columns_ignored` altında raporluyor. Elle çıkarmak gerekmiyor |
| tanımadığımız kolonlar gelmiş | yok sayılıyor ve raporda listeleniyor |
| aynı (öğrenci, tarih) iki kez | **reddedilmiyor**: geçmişte **son** satır tutuluyor, sonuçlarda **en erken** ayrılma tarihi tutuluyor, sayı rapora yazılıyor |
| kategorik seviyeler bizim `CATEGORICAL_LEVELS`'a uymuyor | beklenen durum: o tablo müşteri başına |
| paket adları `PLAN_MONTHS`'ta yok | `add_monthly_value` bilinmeyen paketi uyarıyla 1 ay sayıyor. Parasal bölüm bu tablo doldurulmadan yorumlanamaz |
| aylık değil çeyreklik gözlem | çalışır; `--step-days` ona göre verilir ve erkenlik çözünürlüğü çeyrek olur |
| `monthly_value_try` / `monthly_fee_try`+`plan_type` yok | parasal bölüm raporda hiç çıkmaz, geri kalan her şey çıkar |

Not: `config.MAX_NULL_RATIO_PER_COLUMN` (0.05), `MAX_QUARANTINE_RATIO` (0.10) ve
`QUARANTINE_WARN_RATIO` (0.01) **üretim hattının** kapıları, backtest'in değil.
Backtest kasıtlı olarak daha müsamahakâr: boş bir değer o satırın o kolonunu
kullanmıyor, koşuyu düşürmüyor.

### Kaç ayrılan öğrenci yeter?

Artık bunun bir repo sabiti var: **`config.BACKTEST_MIN_REPORTABLE_N` = 30.**
Otuzdan az öğrenci üzerinde ölçülen her rakam JSON'a `reportable: false` ile
yazılıyor ve raporda **gürültü olarak kelimelerle** anlatılıyor, tek bir sayıya
dönüştürülmüyor.

Gerekçe reponun kendi deneyimi: `precision@20` 0.75'ten 0.35'e düştüğünde
aradaki fark **sekiz öğrenciydi** ve iki güven aralığı birbiriyle örtüşüyordu
([`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md), "precision@20 is twenty coin flips").
Pratik sonuç: **aralığın genişliği raporun ana sayısıdır.**

---

## Kötü veya ince veri: normal durum

Beklenen hâl bu, istisna değil. Altı senaryo, her birinde ne yapılacağı ve
rapora ne yazılacağı.

**1. Geçmiş 90 günden kısa, ya da tek kesit.**
Script koşmuyor. Üç seçenek: (a) daha uzun bir export istemek — ilk tercih;
(b) `--churn-window-days` ve `--step-days`'i düşürmek, ki bu müşterinin kendi
ayrılma tanımını bozmak demek olabilir, o yüzden ancak müşteri onaylarsa;
(c) backtest yerine **yöntem raporu** göndermek. Üçüncüsünü hiç yapmamaktan iyi
sayıyorum: hangi kolonların mevcut olduğu, hangilerinin eksik olduğu, etiket
tanımının yazıya geçmiş hâli ve bir sonuç için ne kadar geçmiş gerektiği tek
sayfada — kurumun kendi veri toplamasına dair en somut geri bildirim.

**2. Kolonların geçmiş penceresi yazılı değil.**
[`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md)'nin "lagged-needed" dediği 12 kolonun
durumu. O audit sentetik tek kesitte çözülemedi; gerçek bir export'ta çözülebilir.
Kuruma sorulacak soru tek: *her kolonun değeri hangi ana ait ve hangi geriye
dönük süreyi topluyor?* Cevap gelmezse kolonlar modelde kalır ama rapor
"hiçbir kolonun geriye-nedensel olduğu **bilinmiyor**" der — "hiçbiri değil"
demez. Script bu uyarıyı `caveats` içine kendisi koyuyor.

**3. Mentor davranışı kolonları gelmiş.**
Elle bir şey yapılmıyor: script `config.AUDITED_OUT_FEATURES`'taki kolonları
tanıyor, kullanmıyor ve raporda listeliyor. Sentetik demo dosyaları bu kolonları
**kasten** içeriyor ki bu davranış iddia edilmek yerine görülebilsin. Müşteri
"neden kullanmadınız" diye sorarsa cevap sayılarla yazılı: tek başına
`days_since_last_contact` 24 feature'lık modelden daha iyi `precision@20`
veriyor (0.900'e karşı 0.750), çünkü mentorun çoktan olmuş bir kopmaya verdiği
tepkiyi ölçüyor.

**4. Etiket tanımı "ödemeyi kesti".**
`payment_delay_days_avg` ve `missed_trial_exam_count` kısmen etiketin kendisi
olur. Eşleme JSON'undan çıkarılır (eşlenmeyen kolon kullanılmıyor), sonra kalan
sayı raporlanır. Çıkarmadan elde edilen sayı yüksek ve sahte olur.

**5. Veri ince: ayrılan sayısı az, ya da fold'lar atlanıyor.**
Script atlanan her değerlendirme noktasını **nedeniyle** raporluyor ("only 8
churner(s) in the training slice, minimum 10"). Rapora "dokuz ay ölçtük" değil,
"dokuz ay ölçtük ve üçü kullanılamadı" yazılır — bunlar farklı iddialar.
`--min-train-rows` / `--min-train-churners` düşürülebilir, ama scriptin kendi
hata mesajının dediği gibi: sadece o kadar az veriyle ölçülmüş bir sayıyı
yayınlamaya razıysan.

**6. Dosya hiç gelmiyor.**
İki hatırlatmadan sonra bırakılır. Veri göndermeyen bir kurum pilot yapmaz; bunu
üç hafta kovalayarak öğrenmek yerine bir haftada öğrenmek daha iyidir.

---

## Koşu

> Bu bölümdeki bayraklar `python scripts/backtest.py --help` çıktısından
> alınmıştır. Yetkili kaynak her zaman `--help` ve `--contract`; bu tablo onların
> özeti. Mekanizmanın **neden** böyle olduğu [`BACKTEST.md`](BACKTEST.md)'de —
> burada tekrarlanmıyor.

### Sıra

**1. Backtest repo DIŞINDA koşar.** Bu bir tavsiye değil, yordamın kuralı:
müşteri dosyası çalışma ağacına (working tree) **hiç girmez**. Girdiler repo
dışında bir dizinde durur ve **çıktı da** `--out` ile oraya yazdırılır:

```
~/eoai-backtests/<musteri>/
    history.csv
    outcomes.csv
    mapping.json
    rapor.json          <- --out buraya
```

Üç ayrı sebep ve üçü de somut:

- **`metrics/` altına yazılan bir müşteri raporu gitignore'lu DEĞİL.**
  `.gitignore` `metrics/train_metrics_*.json` ve `metrics/SENTETIK_backtest_*.json`
  satırlarını taşıyor — ikisi de önek bazlı. Bir müşteri koşusunun varsayılan
  çıktısı (`metrics/backtest_<zaman>.json`) bu desenlerin **hiçbirine uymuyor**,
  yani `git status`'ta görünür ve bir `git add .` ile commit edilebilir. `--out`
  verilmeden koşulan bir müşteri backtest'i bu yüzden tek başına bir risk.
- **`data/` altına konan bir müşteri dosyası imaja girebilir.**
  `.dockerignore` `data/*`'ı dışlıyor ve sadece `!data/daily_data.csv`'yi geri
  alıyor — yani müşteri export'unu **o isimle** kaydetmek onu o checkout'tan
  kurulan her imaja sokar, ve bir imaj makineden çıktıktan sonra geri çağrılamaz.
- **Silinebilirlik.** Tek bir dizinde duran veri silinebilir; repo'nun içine
  dağılmış veri silindiğini **kanıtlayamaz**. Teslim günü verilen söz ancak
  birinci durumda tutulabilir.

`.gitignore`'daki `data/SENTETIK_*` ve `metrics/SENTETIK_backtest_*.json`
satırları **sentetik demo verisi** için: üretici onları ~10 saniyede yeniden
üretiyor, 7.8 MB ve kaynak değil. O satırlar müşteri verisini korumuyor ve
korumak için orada değil.

**2. Sözleşmeyi oku, sonra eşlemeyi yaz.**

```bash
python scripts/backtest.py --contract
```

Eşleme JSON'u müşterinin kolon adlarını bizimkine çeviriyor. Müşterinin
`ayrilma_tarihi` ya da `gozlem_tarihi` demesi sorun değil — `config.py`
düzenlemek gerekmiyor, eşleme dosyası bu iş için var:

```json
{
  "history":  { "ogrenci_no": "student_id", "gozlem_tarihi": "as_of_date" },
  "outcomes": { "ogrenci_no": "student_id", "ayrilma_tarihi": "churn_date" },
  "date_format": "%d.%m.%Y",
  "churn_true_values": ["Evet"],
  "churn_false_values": ["Hayir"]
}
```

`"_"` ile başlayan anahtarlar yorum sayılıyor — müşteriye özgü notları oraya
yazabilirsin. `date_format` opsiyonel; verilmezse ISO okunup pandas'a düşülüyor.

**3. Koş.**

```bash
python scripts/backtest.py \
    --history  ~/eoai-backtests/<musteri>/history.csv \
    --outcomes ~/eoai-backtests/<musteri>/outcomes.csv \
    --mapping  ~/eoai-backtests/<musteri>/mapping.json \
    --out      ~/eoai-backtests/<musteri>/rapor.json
```

`--out` **atlanmaz**: varsayılanı `config.BACKTEST_OUTPUT_DIR`, yani repo içindeki
`metrics/`, ve orada gitignore'lu değil (yukarı bak). Okunur özet Türkçe
basılıyor. **Müşteri raporu o JSON'dan üretilir; script raporu yazmıyor.**

**4. Parametre duyarlılığı.** Tek bir koşu bir sayı verir, üç koşu o sayının ne
kadar sağlam olduğunu verir.

| bayrak | varsayılan | ne zaman değiştirilir |
|---|---|---|
| `--churn-window-days` | `config.CHURN_WINDOW_DAYS` = 30 | müşterinin ayrılma tanımı başka bir pencere söylüyorsa |
| `--step-days` | 30 | erkenlik çözünürlüğü bu. 30 günlük adımda 10 gün önce gelen uyarı ile 30 gün önce gelen uyarı **aynı ölçüm** |
| `--k` | `config.PRECISION_AT_K` = 20 | müşterinin gerçek mentor kapasitesi. Hem isabeti hem kapsama tavanını belirliyor |
| `--flag-rule` | `capacity` | `threshold` üretim hattının maliyet-optimal eşiğini kullanır. 30 günlük pencerede taban oran %3–9 ve 1:3 maliyetle eşik neredeyse kimseyi işaretlemiyor, yani o rakam modeli değil maliyet ayarını tarif eder. **`capacity` kalsın** |
| `--active-window-days` | 0 = export'un medyan gözlem aralığının iki katı | bir öğrencinin son satırı ne kadar eski olup da hâlâ skorlanabilir |
| `--warmup-days` | 0 = bir pencere + bir adım | ilk değerlendirme noktasının arkasında ne kadar geçmiş olmalı |
| `--baseline-column` | `src/model/baseline.py` varsayılanı | karşılaştırılan tek kolonlu kural |
| `--min-train-rows` / `--min-train-churners` | 200 / 10 | fold atlanma eşikleri. Düşürmek = daha az veriyle ölçülmüş bir sayı yayınlamayı kabul etmek |
| `--bootstrap-resamples` / `--seed` | 2000 / 42 | aralıkların üretimi |
| `--allow-partial-outcomes` | kapalı | sonuçlar dosyası geçmişteki öğrencilerin yarısından azını kapsıyorsa |
| `--out` | `metrics/` | JSON'un yolu |

**5. `is_synthetic_data`.** Script bunu girdiden okuyor: her iki CSV'nin her
satırında `config.SYNTHETIC_MARKER_COLUMN` varsa rapor `SENTETIK_` önekiyle
yazılıyor ve her sayının üstüne/altına uyarı basılıyor. Üretim eğitim hattındaki
sabit `True` sorunu **backtest'i etkilemiyor** — ama müşteriye bir
`model_meta.json` eki koyacaksan orada hâlâ geçerli.

### Müşteri verisi gelmeden önce denemek

```bash
python scripts/make_synthetic_history.py        # data/SENTETIK_backtest_* yazar
python scripts/backtest.py \
    --history  data/SENTETIK_backtest_history.csv \
    --outcomes data/SENTETIK_backtest_outcomes.csv \
    --mapping  data/SENTETIK_backtest_mapping.json
```

Üretici bayrakları: `--snapshot`, `--out-dir`, `--end-date`, `--months`,
`--observation-step-days`, `--drift-days`, `--noise-fraction`, `--seed`.

Bu dosyaların gösterdiği şey: walk-forward makinesinin iki CSV'den bir JSON
rapora kadar gerçekten koştuğu. Göstermediği şey: doğrulukla ilgili **hiçbir
şey.** Ayrılacak öğrencilerin feature'ları, ayrılmalarından `--drift-days` (90)
gün önce üretici tarafından **bilerek** kaydırılıyor; modelin o kaymayı tekrar
bulması kanıt değil aritmetiktir. `--drift-days` aynı zamanda bu dosyalardan
çıkabilecek erkenliğin **tavanı**.

Üretilen hiçbir satır bir müşteri raporuna girmez. Aynı kural
`scripts/seed_demo_history.py` için de yazılı.

---

## Çıktıyı okumak

Script yedi numaralı bölüm basıyor. Aşağıda her biri, ve hepsinde aynı sentetik
koşudan bir örnek.

> **Aşağıdaki bütün sayılar SENTETİK** —
> `metrics/SENTETIK_backtest_20261003T163104Z.json`, girdisi
> `make_synthetic_history.py` ile üretilmiş yapay bir 12 aylık geçmiş.
> **Hiçbiri müşteriye veya yatırımcıya gösterilemez.** Burada sadece her sayının
> nasıl okunacağını göstermek için duruyorlar.

Koşunun çerçevesi (SENTETİK): 3500 öğrenci, 41.331 satır, 2025-09-30 → 2026-09-29,
medyan gözlem aralığı 14 gün, 952 ayrılma. 10 değerlendirme noktası kuruldu,
**9'u kullanılabilir**, 1'i atlandı ("only 8 churner(s) in the training slice,
minimum 10"). 19 feature kullanıldı, 0 feature gelmedi, 3 kolon
(`mentor_contact_freq_per_month`, `days_since_last_contact`, `days_to_next_exam`)
tanındı ve **kullanılmadı**.

### 1) Erken uyarı süresi — "satışı yapan sayı"

**SENTETİK:** işaretlenen 32 ayrılan öğrenci için ilk işaretten ayrılmaya kadar
medyan **22 gün**, Q1 **16** / Q3 **32**, aralık 3–45, medyanın %95 güven aralığı
**18–28**. (Ekrandaki Q1 yuvarlanmış; JSON'daki ham değer 15.5.)

Nasıl okunur: **medyan ve çeyrekler yazılır, ortalama yazılmaz.** Dağılım
çarpıksa ortalama kimsenin yaşamadığı bir sayıdır (burada ortalama 23.1,
medyan 22 — yakın, ama bu garanti değil). `n` her zaman yanında durur: 32 kişi
üzerinde ölçülmüş bir medyan, 906 kişilik bir popülasyonun medyanı değildir.

Çözünürlük uyarısı scriptin kendi `caveats`'inde: **adım 30 gün olduğu için 10
gün önce gelen uyarı ile 30 gün önce gelen uyarı aynı ölçüm.** Bundan kısa bir
erkenlik bu ölçümde görünmez. Sentetik veride ek bir tavan daha var: üreticinin
`--drift-days` değeri 90, yani 90 günden uzun bir erkenlik bu dosyalardan
çıkamaz.

### 2) Kapsama — ve neden tavana göre okunur

**SENTETİK:** ölçülebilir ayrılma 906 / 924, hiç skorlanamayan 18, kapsama
**%3.5 [%2.3–%4.8]**, ölçülen dönem 2026-01-02 → 2026-09-29. **Kapasite tavanı
%19.9** (180 işaretleme hakkı, 906 ayrılma). Tavanın ne kadarı: **%17.8**.

Bu, raporun en kolay yanlış anlaşılan ve **her iki yöne** çarpıtılabilen sayısı.
%3.5'i 100'e göre okumak "ayrılmalarınızın %96.5'ini kaçırdık" demek olur ve bu
bir model başarısızlığı gibi görünür. Değil: **aritmetik.** Dönemde 20 kişilik
bir liste × 9 dönem = 180 işaretleme hakkı var, 906 ayrılmaya karşı. Kusursuz bir
sıralama — her slotu henüz kapsanmamış bir ayrılana harcayan bir kâhin — bile en
fazla **%19.9** kapsayabilirdi.

`oracle_coverage()` bu tavanı hesaplıyor ve rapor üç sayıyı birlikte basıyor:
ulaşılan kapsama, tavan, ve tavanın ne kadarına ulaşıldığı. **Kapsama her zaman
tavana göre okunur, 100'e göre değil.** Müşteriye söylenecek cümle "ayrılanların
%3.5'ini kapsadık" değil, "mentorlarınızın kapasitesiyle en fazla %19.9'u
kapsanabilirdi, biz onun %17.8'ine ulaştık" olmalı — ve ikinci cümle hem daha
dürüst hem de müşterinin kapasite konuşmasını başlatan şey.

Hiç skorlanamayan 18 ayrılan **kapsama hesabına girmiyor**: son gözlemleri
aktiflik penceresinin dışında kalmış, yani onları kaçırmak modelin değil
müşterinin veri boşluğunun sonucu. Girerse modeli müşterinin kendi eksiğiyle
suçlamış oluruz.

### 3) Kapasiteye göre isabet

**SENTETİK:** `precision@20` = **0.139 [0.083–0.183]**, `lift@20` = **2.98x**
(fold'lar arası 1.60–4.85).

Nasıl okunur: isabet **tek başına** yazılmaz, yanında iki şey durur — **güven
aralığı** ve **lift**. 0.139, "20 isim verince yaklaşık 3'ü gerçekten
ayrılacak" demek. Tek başına kötü görünür. Lift 2.98x ise "tesadüfi seçimden
üç kat iyi" demek, ve bu taban oranı içeriyor: 30 günlük pencerede taban oran
%3–9 bandında, yani bu problem doğası gereği düşük isabetli.

Lift'in 1.0 olması "tesadüfi seçimden daha iyi değil" demek ve script bunu
çıktıda yazıyor. Fold'lar arası aralığın geniş olması (1.60–4.85) tek bir
sayının ne kadar sağlam olduğunu söylüyor — rapora minimum ve maksimum da girer.

### 4) Parasal büyüklük

**SENTETİK:** işaretlenen 144 öğrenci, aylık değeri 239.760 TL; yakalanan 32
ayrılan, onların aylık değeri 54.854 TL.

Burada tek bir kural var ve istisnası yok: bu tutar **risk altında ve görünür
hâle gelen** aylık değerdir, **kurtarılan gelir değildir.** Script bunu JSON'a
`value_at_risk_not_value_saved` alanıyla yazıyor ve terminalde her parasal
rakamın altına basıyor. Alan adı bir slogan değil, bir koruma: müşteri raporu bu
JSON'dan üretildiği için uyarı rakamla aynı dosyada yolculuk ediyor.

TL rakamı rapora **girebilir**, ama sadece bu çerçeveyle. "Kurtarılan gelir",
"ROI", "geri dönüş süresi", "kurtarılan öğrenci × öğrenci başına gelir" —
hiçbiri bu sayıdan çıkmaz. Parasal bölüm `monthly_value_try` ya da
(`monthly_fee_try` + `plan_type`) gelmediyse raporda hiç çıkmıyor.

### 5) Kaçırdıklarımız — "raporun en dürüst bölümü"

**SENTETİK:** hiç işaretlenmeyen ayrılan **874 / 906 (%96.5)**, kaçırılan aylık
değer 1.460.170 TL. En çok kaçırılan sınıf: `12. Sınıf` %97.3 (404/415).
Sayısal kırılım:

| feature | kaçırılan ort. | yakalanan ort. | standart fark |
|---|---|---|---|
| Memnuniyet puanı (1-5) | 3.46 | 2.81 | +0.88 |
| Program uyum oranı | 0.641 | 0.546 | +0.77 |
| Kaçırılan deneme sayısı | 1.55 | 2.31 | −0.69 |

Bu tablo, raporun müşteriyle en uzun konuşulacak parçası ve nedeni şu:
**kaçırdığımız ayrılanların memnuniyeti ve program uyumu, yakaladıklarımızdan
daha yüksek.** Yani model "mutsuz ve uyumsuz öğrenci ayrılır" diyen bir sinyal
bulmuş; kaçırdığı öğrenciler o profile hiç uymayanlar — memnun görünen, programa
uyan, ama yine de giden öğrenciler.

Bu bir başarısızlık itirafı gibi okunabilir ve bir ölçüde öyle. Ama müşteri için
**gerçek bir bulgu**: "bu öğrenciler sizin verinizdeki hiçbir davranış sinyaliyle
görünmüyorlar, yani ayrılma sebepleri topladığınız kolonların dışında" cümlesi,
kurumun kendi veri toplamasına dair söylenebilecek en değerli şey ve pilot
konuşmasını açan kapı.

Bu bölüm raporda **küçültülmez, sona atılmaz, eke saklanmaz.** Scriptin kendi
başlığı "raporun en dürüst bölümü" diyor ve o başlık ciddiye alınmalı.

### 6) Basit kural karşılaştırması

**SENTETİK:** kural = tek kolonla sırala (`message_response_time_hours`), eşit
kapasitede. Kural `precision@K` = **0.039 [0.013–0.068]**, kural kapsama %0.8,
kural erkenlik medyanı 11 gün (n=7). Hüküm: `model_better` — *"Model basit kuralı
geçiyor: precision@K 0.139 vs 0.039, güven aralıkları ayrışıyor. Kapsama farkı
+%2.8."*

Nasıl okunur: kural **eşit kapasitede** koşuyor, yani modelin işaretlediği kadar
öğrenci işaretliyor — karşılaştırma keyfî bir eşikte değil aynı mentor
kapasitesinde. Yönü her fold'un kendi eğitim diliminde yeniden türetiliyor.

Üç olası hüküm ve üçünün de rapora aynı belirginlikte yazılması gerekiyor:
- **aralıklar ayrışıyor ve model önde** → model kazandı, söylenebilir;
- **aralıklar örtüşüyor** → fark **bu veriyle ölçülemiyor**. "Model biraz daha
  iyi" denmez;
- **kural önde** → model kaybetti, ve bu düz Türkçeyle 6. bölümün başına yazılır.

Son durum `scripts/compare_feature_sets.py`'nin kabul kuralıyla aynı yere
çıkıyor: **CatBoost basit modeli geçmek zorunda, yoksa modelin bir değeri yok.**
Sentetik anlık görüntüde geçemiyordu (PR-AUC 0.369'a karşı 0.379). Bir backtest'in
bizi utandıramıyor olması onu kanıt olarak değersiz kılar — bu bölüm o yüzden var
ve o yüzden gizlenmez.

### 7) Uyarılar ve sınırlar

Script `caveats` listesini kendisi üretiyor ve müşteri raporuna **olduğu gibi**
girer. Sentetik koşuda on madde çıktı; aralarında: erkenlik çözünürlüğünün 30 gün
olduğu, kimsenin müdahale etmediği, feature'ların tarihe uygunluğunun müşterinin
beyanı olduğu, kullanılmayan kolonların hangileri olduğu, kapsamanın kapasiteyle
sınırlı olduğu, 18 öğrencinin hiç skorlanamadığı, 1 fold'un atlandığı.

Bu liste kısaltılmaz. Scriptin ürettiği her uyarı, raporda o uyarıyı gerektiren
bir sayı olduğu için üretilmiş.

### Bu rapordan çıkmayan şeyler

Rapor kalibrasyon (Brier, kalibrasyon eğrisi) **basmıyor** — `churn_probability`
bir olasılık olarak sunulacaksa o ölçüm ayrıca yapılmalı. Taban oran da ayrı bir
satır olarak basılmıyor; lift'in içinde dolaylı olarak var. İkisi de rapora elle
eklenebilir, ama scriptin çıktısında yok, dolayısıyla "rapor şöyle diyor"
denemez.

---

## Sunum: ne söylenir, ne söylenmez

### Söylenmeyecek cümleler

Yazılmaz, sözlü olarak da söylenmez. Hiçbiri bu testten çıkmaz:

- "Bu sistem kurulu olsaydı **N öğrenci kurtarılırdı**."
- "**X TL** gelir korunurdu." / herhangi bir ROI, geri dönüş süresi, kurtarılan
  ciro hesabı. Parasal bölümdeki TL *risk altında ve görünür hâle gelen* değer;
  başka bir şeye dönüştürülemez.
- "Ayrılmaların **%Y'sini önlüyoruz**." — önlemek ile görmek aynı şey değil.
- "Model ayrılmaları **%Z doğrulukla tahmin ediyor**." — doğruluk bu problemde
  anlamsız: her şeye "ayrılmaz" diyen bir sistem %3–9 taban oranda %91–97
  "doğru" olur.
- "**Ayrılanlarınızın sadece %3.5'ini kapsadık**" ya da tersi, "**%96.5'ini
  kaçırdık**" — ikisi de kapasite tavanını atlıyor ve ikisi de yanlış. Kapsama
  tavana göre söylenir.
- "Mentorlarınız bu listeyi arasa ayrılma **düşer**." — test edilmemiş bir
  nedensellik iddiası.
- "Model basit kuraldan **biraz daha iyi**" — aralıklar örtüşüyorsa fark
  ölçülememiştir.

### Söylenecek cümleler

- "Ayrılan öğrencilerinizin **N tanesi**, ayrılmadan **medyan M gün önce**
  (çeyrekler Q1–Q3), günlük listenin ilk **K** sırası içinde görünüyordu."
- "İlk K isimde isabet **%P [alt, üst]**, tesadüfi seçimin **L katı**."
- "Mentorlarınızın kapasitesiyle en fazla **%T** kapsanabilirdi; biz onun
  **%S'ine** ulaştık."
- "Kaçırdıklarımız şunlar ve ortak özellikleri şu: **memnuniyetleri ve program
  uyumları yakaladıklarımızdan yüksek.** Yani bu öğrenciler sizin topladığınız
  davranış kolonlarında görünmüyorlar."
- "Kimse müdahale etmediği için bu, **ayrılmanın verinizde önceden görünür
  olduğunu** gösterir. Müdahalenin işe yarayıp yaramadığını göstermez — onu
  ancak bir pilot gösterir."
- "Şu kolonlar gelmediği için şu sinyaller ölçülemedi." — eksik, zayıflık değil
  bulgudur.
- "Şu kolonları gönderdiniz ama **kullanmadık**, çünkü mentorun davranışını
  ölçüyorlar. Gerekçesi yazılı, isterseniz paylaşırız."
- "Bu sayıları üreten yöntem şu dosyada yazılı ve kendi ekibiniz tekrar
  koşturabilir." — [`LEAKAGE_AUDIT.md`](LEAKAGE_AUDIT.md) ile aynı mantık: vaat
  edilen şey yöntem, rakam değil.

### Raporun şekli

Bir sayfa + ek. Sayfada: erkenlik medyanı ve çeyrekleri, ilk K'da isabet +
aralığı + lift, kapsama **ve tavanı**, kaçırdıklarımızın oranı ve profili, ve
"kurtarıldı değil görüldü" cümlesi. Ekte: etiket tanımı (onların cümlesiyle),
kullanılan / gelmeyen / kullanılmayan kolonlar ve nedenleri, atlanan fold'lar ve
nedenleri, basit kural karşılaştırması ve hükmü, segment kırılımı, scriptin
`caveats` listesi olduğu gibi.

Sayıları göndermeden önce **ikinci kez** doğrula. Bir backtest raporunda
düzeltme yayınlamak, düşük bir sayı yayınlamaktan daha pahalı.

---

## Veri hijyeni ve teslimde silme

Aday kurum hiçbir şeye söz vermemiş durumda bir veri gönderdi, ve ona
[`VERI_TALEBI.md`](VERI_TALEBI.md)'de dört maddelik bir söz verildi. Bu bölüm o
sözün operasyonel karşılığı — ve sonunda, tutulamayan kısmı.

### Dosya gelmeden önce: bir kez kurulur

Sözün yarısı dosya geldikten sonra yapılamaz. Önceden hazır olması gerekenler:

- **Şifreli disk.** Analiz yapılan makinenin disk şifrelemesi açık olmalı
  (FileVault / LUKS). Dosya geldikten sonra şifrelemeyi açmak o dosyayı
  şifrelemiyor.
- **Yedeklemenin kapsamı.** `~/eoai-backtests/` dizini otomatik yedeklemenin
  (Time Machine, iCloud/Drive/Dropbox senkronizasyonu) **dışında** olmalı.
  Bu, sözün en kolay ihlal edilen maddesi: bir bulut senkronizasyonu dizini
  kapsıyorsa "bulut hizmetine girmiyor" sözü dosya daha oraya konmadan
  bozulmuş olur, ve yedekten silmek elle silmekten çok daha zor.
- **Dizin.** Müşteri başına tek bir dizin, repo dışında. Dağılmış kopya
  silindiğini kanıtlayamayan kopyadır.

### Dosya elimizdeyken

- **Çalışma ağacına hiç girmez.** Girdiler ve `--out` çıktısı repo dışında.
  Sebepleri [Koşu, adım 1](#sıra)'de sayıldı: `metrics/` altındaki bir müşteri
  raporu gitignore'lu değil, ve `data/daily_data.csv` adıyla kaydedilen bir
  export imaja girer.
- **Tek kişi açar.** Analizi yapan kişi dışında kimseye açılmıyor — ekran
  paylaşımlı bir görüşmede dosyayı açmak da "açmak" sayılır.
- **Hiçbir model eğitimine girmez.** `running_train_pipeline.py` bu veriye hiç
  doğrultulmaz. Backtest kendi modelini fold başına kendi içinde eğitiyor ve
  `saved_models/` içine **hiçbir şey yazmıyor** — yani kazara kalıcı bir model
  üretmiyor. Bu, scriptin davranışı olduğu için sözün bu maddesi dikkatle değil
  tasarımla tutuluyor.
- **Log'lara girmez.** `validate()` tekrarlayan id'lerin sayısını yazıyor,
  id'leri yazmıyor; karantina kolon adı ve sayı yazıyor. `docs/LOGGING.md` neyin
  yazıldığını ve 30 günlük saklama süresini anlatıyor.
- **`catboost_info/` artığı.** CatBoost eğitim esnasında çalışma dizinine
  `catboost_info/` yazıyor (`learn_error.tsv`, `time_left.tsv`). İçinde öğrenci
  satırı yok, sadece iterasyon metrikleri — ama silinecekler listesine girer.

### Rapor öğrenci satırı içermiyor — ve içerdiği şey

Söz bu: *"Rapor da öğrenci bazlı satır içermiyor."* Doğrulandı: sentetik koşunun
JSON raporunda tek bir öğrenci kimliği geçmiyor. Rapor fold başına toplamlar,
oranlar, segment kırılımları ve güven aralıkları taşıyor.

Ama "hiçbir şey taşımıyor" demek yanlış olur ve bunu bilerek söylemek gerekiyor:
rapor **onların verisinden türetilmiş topluluk istatistikleri** taşıyor — fold
başına imputasyon medyanları, taban oranlar, segment bazlı ayrılma sayıları, ve
girdi dosyalarının `sha256` özetleri. Bunlar kimseyi tanımlamıyor, ama kurumun
kendi iş rakamları. Rapor onlara gidiyor, o yüzden sorun değil; bir yerde örnek
olarak gösterilecekse anonimleştirilmesi gereken şey bu bölümler.

### Teslim günü: silme yordamı

Rapor gönderildikten sonra, **aynı gün**, sırayla:

1. `~/eoai-backtests/<musteri>/` — iki CSV, eşleme JSON'u, rapor JSON'u, varsa
   zip ve açılmış hâli.
2. Duyarlılık koşularının çıktıları — `--step-days` / `--k` denemelerinden kalan
   her JSON. `--out` atlandıysa `metrics/` altına da bakılır.
3. `catboost_info/`.
4. Ara çalışma dosyaları: notebook çıktıları, grafik PNG'leri, kopyalanmış
   parçalar, ekran görüntüleri.
5. Dosyanın geldiği yer: e-posta eki — **gönderilenler ve çöp kutusu dahil** —
   ya da paylaşım bağlantısıyla indirilmiş kopya.
6. Çöp kutusu / `~/.Trash` boşaltılır. "Sildim" ile "çöp kutusunda duruyor" aynı
   şey değil.
7. Yedeklemenin o dizini kapsamadığı **tekrar** doğrulanır.

Elde kalması gereken tek şey: **raporun kendisi** ve etiket tanımı gibi
müşteriye ait olmayan notlar. Öğrenci satırı kalmaz.

### Bu sözün tutulamayan kısmı

Dürüst olmak gerekiyor, çünkü söz zaten verildi ve bir kez verilmiş bir söz
ancak önceden düzeltilebilir.

**"Siliyoruz" dosya sistemi düzeyinde "silindiğini kanıtlıyoruz" değildir.**
Normal bir `rm` ve boşaltılmış bir çöp kutusu, SSD üzerinde bloğun üzerine
yazıldığını garanti etmiyor; TRIM ve wear-leveling bunu işletim sistemine
bırakıyor. Şifreli bir disk bu riski büyük ölçüde kapatıyor (anahtar olmadan
kalan blok okunamaz) — **şifreli disk maddesinin asıl işlevi bu**, ve bu yüzden
silme sözünün ön koşulu.

**Üç yer sözü sessizce bozabilir ve üçü de önceden kontrol edilmeli:**
bir bulut senkronizasyonu dizini kapsıyorsa (dosya artık bizim makinemizde
değil), bir otomatik yedekleme kapsıyorsa (eski kopya yedekte yaşamaya devam
eder), ve e-posta sağlayıcısının sunucusu (ek silinse bile sağlayıcının
saklama/arşiv politikası geçerli — gönderenin kutusundaki kopya hiç bizim
elimizde değil).

Son madde özellikle önemli: **adayın kendi gönderilmiş e-postasındaki kopyayı
silemiyoruz.** Verilen söz bizim elimizdeki kopyayı kapsıyor ve kapsadığı kadarını
söylemeli. Aday "siz sildiniz mi" diye sorarsa cevap "elimizdeki her kopyayı
sildik" olur — "veri artık hiçbir yerde yok" olmaz, çünkü o bizim
söyleyebileceğimiz bir şey değil.

Pratik sonuç: dosyaların **paylaşım bağlantısıyla** gelmesi e-posta ekinden
iyidir — bağlantı onların tarafında iptal edilebilir, e-posta eki iki tarafın
posta kutusunda birden yaşar. Bu, `VERI_TALEBI.md`'nin "Dosyaları nereye
göndereceksiniz" bölümü doldurulurken verilecek bir karar.

Pilot başlarsa veri yeniden istenir. Bu, teslimde net bir söz verebilmenin
bedeli.

---

## Senin kararların

Ücret, veri akıbeti ve NDA artık [verilmiş kararlar](#verilmiş-kararlar) —
burada değiller. Kalanlar:

| karar | seçenekler ve her birinin anlamı |
|---|---|
| **Aynı anda kaç backtest** | Script müşteriye özgü bir `config.py` düzenlemesi **gerektirmiyor** (eşleme JSON'u o işi yapıyor), yani v1'deki "ayrı checkout" kısıtı büyük ölçüde kalktı. Kalan kısıt insan: iki paralel uygunluk kontrolü ve iki rapor aynı hafta yazılabiliyor mu |
| **Veri 90 günden kısaysa devam** | (a) daha uzun export istemek; (b) pencereyi/adımı düşürmek — müşterinin tanımını bozma riskiyle; (c) yöntem raporu göndermek. Üçü de savunulabilir, biri seçilmeli |
| **Ayrılan sayısı 30'un altındaysa devam** | Script `reportable: false` ile rakamı gürültü ilan ediyor ama koşuyu durdurmuyor. Böyle bir raporu teslim etmek mi, teslim etmeden önce daha fazla veri istemek mi |
| **Basit kural kazanırsa ne söylenir** | Rapor hükmü düz Türkçeyle yazıyor. Soru şu: o raporla aynı görüşmede pilot konuşulur mu, yoksa önce model mi düzeltilir |
| **Kaçırılanların profili ne kadar öne çıkarılır** | Bölüm 5 müşteri için en değerli, bizim için en riskli parça. Ana sayfaya mı girer, eke mi — ben ana sayfa diyorum, karar senin |
| **Pilot ne kadar sürer / başarı kriteri ne** | [`PILOT_KURULUM.md`](PILOT_KURULUM.md)'de; backtest görüşmesinde sorulacağı için cevabın hazır olması gerekiyor |

---

## Hızlı kontrol listeleri

### Veri geldiği gün

- [ ] kişisel veri var mı — varsa dosya işlenmeden geri gider **(script bunu
      kontrol etmiyor, bu bir insan adımı)**
- [ ] `student_id` ve `as_of_date` geçmişte var mı
- [ ] `student_id` ve `churn_date` sonuçlarda var mı
- [ ] geçmiş kaç gün kapsıyor — **90'dan azsa script koşmaz**
- [ ] kaç feature kolonu geldi — **3'ten azsa script koşmaz**
- [ ] sonuçlar dosyası geçmişteki öğrencilerin yüzde kaçını kapsıyor — **%50'nin
      altında `--allow-partial-outcomes` gerekir**
- [ ] ayrılan sayısı kaç — **30'un altındaysa rakamlar `reportable: false`**
- [ ] etiket tanımı yazılı mı (onların cümlesiyle)
- [ ] pencere uzunluğu yazılı mı → `--churn-window-days`
- [ ] mentor kapasitesi yazılı mı → `--k`
- [ ] etiketin kendisi olan kolon var mı (`payment_delay_days_avg`,
      `missed_trial_exam_count` ve benzerleri) — eşlemeden çıkarılacak
- [ ] `--contract` çıktısı okundu, eşleme JSON'u buna göre yazıldı
- [ ] analiz makinesinin **disk şifrelemesi açık**
- [ ] dosya repo dışında, tek bir dizinde (`~/eoai-backtests/<musteri>/`)
- [ ] o dizin otomatik yedeklemenin ve bulut senkronizasyonunun kapsamında
      **değil** — dosya konmadan önce doğrulandı
- [ ] `--out` repo dışına ayarlandı

### Rapor göndermeden önce

- [ ] "kurtarıldı" anlamına gelen tek bir cümle yok
- [ ] ROI, geri dönüş süresi, kurtarılan ciro yok; TL varsa sadece "risk altında
      ve görünür" çerçevesinde
- [ ] her isabet oranının yanında güven aralığı var
- [ ] kapsama **tavanıyla birlikte** yazılmış, 100'e göre değil
- [ ] erkenlik medyan + çeyrek olarak yazılmış, ortalama olarak değil
- [ ] erkenliğin 30 günlük çözünürlük sınırı yazılı
- [ ] kaçırdıklarımız bölümü ana sayfada ve küçültülmemiş
- [ ] basit kural karşılaştırması ve hükmü ekte; aralıklar örtüşüyorsa "fark
      ölçülemedi" denmiş
- [ ] gelmeyen ve kullanılmayan kolonlar listelenmiş
- [ ] atlanan fold'lar nedenleriyle yazılmış
- [ ] scriptin `caveats` listesi kısaltılmadan eklenmiş
- [ ] sentetik bir koşudan hiçbir sayı sızmamış
- [ ] sayılar ikinci kez doğrulandı

### Teslim günü

- [ ] rapor gönderildi
- [ ] raporda öğrenci bazlı satır olmadığı kontrol edildi
- [ ] `~/eoai-backtests/<musteri>/` silindi (girdiler + rapor JSON'u)
- [ ] duyarlılık koşularının çıktıları silindi; `--out` atlanmışsa `metrics/`
      altına da bakıldı
- [ ] `catboost_info/` silindi
- [ ] ara dosyalar, ekran görüntüleri, grafikler silindi
- [ ] e-posta eki silindi — **gönderilenler ve çöp kutusu dahil** — ya da
      indirilmiş kopya silindi
- [ ] çöp kutusu boşaltıldı
- [ ] yedekleme kapsamı tekrar doğrulandı
- [ ] elde sadece rapor ve müşteriye ait olmayan notlar kaldı
- [ ] aday "sildiniz mi" diye sorarsa verilecek cevap: **"elimizdeki her kopyayı
      sildik"** — "veri artık hiçbir yerde yok" değil

---

## Doğrulanamayanlar ve çakışmalar

Bu dosya yazılırken tespit edilen, **başka dosyalarda** düzeltilmesi gereken
şeyler. Hiçbirine burada dokunulmadı.

1. **`VERI_TALEBI.md` "En az hâli" tablosu**, *"sadece tek bir güncel kesit +
   ayrılma kayıtları → sıralama testi yapabiliriz"* diyen bir satır taşıyor.
   Script bunu **reddediyor**: tek kesitte tek bir değerlendirme noktası bile
   kurulamıyor (en az 90 gün gerekiyor). Aynı tablonun son satırı da
   *"davranış kolonu hiç yok → paket/süre/sınıf ile ilişki"* diyor; üçten az
   opsiyonel kolonla script hiç koşmuyor. İki satır da düzeltilmeli, yoksa
   tutamayacağımız bir şeyi vaat eden bir talep göndermiş oluyoruz.
2. **`VERI_TALEBI.md` "Raporda olmayacak şey"** maddesi *"ya da bir TL hesabı"*
   diyor. Script bir parasal bölüm **basıyor** ("risk altında ve görünür hâle
   gelen aylık değer"). Cümle, TL'nin tamamen yokluğunu değil kurtarılan gelir
   olmadığını söyleyecek şekilde düzeltilmeli — aynı dosyanın "Veriye ne oluyor"
   bölümü raporun "toplamlar, oranlar, segment kırılımları" taşıdığını zaten
   söylüyor, yani düzeltme o cümleyle tutarlı olur.
3. **`VERI_TALEBI.md` "iki CSV"** diyor; sözleşme üç dosya sayıyor (geçmiş,
   sonuçlar, eşleme JSON'u). Eşlemeyi pratikte biz yazdığımız için adaya "iki
   CSV" demek yanlış değil — ama adayın **kolon adlarını** bir yerde söylemesi
   gerekiyor ve talep bunu açıkça istemiyor. "Dosyanın başlık satırını da
   gönderin" diyen bir cümle eşleme turunu tamamen kaldırır.
4. **`VERI_TALEBI.md` "Dosyaları nereye göndereceksiniz"** hâlâ
   `[İLETİŞİM — doldurulacak]`. Doldurulmadan dosya gönderilemez; ayrıca
   [silme bölümündeki](#bu-sözün-tutulamayan-kısmı) gerekçeyle **paylaşım
   bağlantısı** e-posta ekine tercih edilmeli ve o tercih bu cümleye yazılmalı.
5. **`PILOT_KURULUM.md`** bu scriptler yokken yazıldı: bölüm 11'deki not hâlâ
   "backtest.py'nin bayrakları doğrulanmadı" diyor ve bölüm 10'un karar tablosu
   backtest'in ücretli olup olmamasını açık soru sayıyor. İkisi de artık geçerli
   değil.
6. **Doğrulanamayan tek şey kaldı:** feature değerlerinin her satırda o tarihte
   geçerli olan değerler olduğu. Bu müşterinin beyanı, dosyadan kontrol edilemez,
   ve yanlışsa **bütün sayılar yukarı sapar**. Script bunu `caveats` içine kendisi
   koyuyor; raporda da müşterinin kendi diliyle, etkilediği sayıların yanında
   durmak zorunda.
