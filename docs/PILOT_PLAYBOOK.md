# EO-Churn — Pilot Playbook

Müşteriyle pilot konusunda **anlaştıktan sonra** ne yapacağımızı adım adım anlatan
dosya. Sözleşme imzalandığı gün buradan başlanır.

**Kimin için:** EOAI Lab ekibi. Müşteriye gösterilmez; müşteriye giden metinlerin
şablonları eklerde.

**Temel ilke:** Pilot bir kurulumla başlamaz, **geriye dönük bir testle** başlar.
Müşterinin sistemine hiçbir şey kurmadan, geçmiş verisiyle "bu sistem geçen yıl
çalışıyor olsaydı ne olurdu" sorusunu cevaplıyoruz. En hızlı, en ucuz, KVKK riski
en düşük ve en ikna edici kanıt bu. Doğrudan canlıya kurmaya çalışmak klasik
hatadır: aylar sürer, IT'ye takılır, ve sonunda model zayıfsa bütün emek çöpe
gider.

| Faz | Süre | Çıktı | Müşterinin sistemine dokunuyor mu? |
|---|---|---|---|
| **-1** Anlaşma netleştirme | 2-3 gün | İmzalı mutabakat | Hayır |
| **0** Geriye dönük test | 1-2 hafta | Backtest raporu + go/no-go | Hayır |
| **1** Gölge mod | 4-6 hafta | Günlük çalışan sistem, kimse aksiyon almıyor | Evet, tek yönlü okuma |
| **2** Kontrollü müdahale | 6-8 hafta | Kontrol grubuna karşı ölçülmüş etki | Evet |
| **3** Karar | 1 hafta | Abonelik ya da temiz çıkış | — |

Toplam 3-4 ay. Her fazın sonunda bir **kapı** var: geçilmezse durulur. Kapıyı
geçemeyen bir pilotu sürüklemek, iki tarafın da zamanını yakar.

---

## Faz -1 — Anlaşma imzalanmadan netleşmesi gerekenler

İmzadan önce bu altı maddenin yazılı cevabı olmalı. Yoksa pilot sonunda "başarılı
mıydı" tartışması çıkar ve o tartışmayı müşteri kazanır.

- [ ] **Başarı kriteri, sayı olarak.** Örnek: *"Günlük 40 kişilik listede,
      ayrılan öğrencilerin en az %55'i, ayrılmadan en az 14 gün önce yer alacak."*
      Muğlak bırakmak ("faydalı olsun") pilotun en yaygın ölüm sebebi.
- [ ] **Başlangıç ve bitiş tarihi.** Süresiz pilot = sonsuz pilot.
- [ ] **Veri teslim tarihi ve sahibi.** Kim, hangi gün, hangi dosyayı verecek.
      İsim ve unvan yaz. Pilotları en çok öldüren şey: veri 6 hafta gelmez.
- [ ] **Müşteri tarafında bir sahip.** Sponsor (imzayı atan) değil; **haftalık
      toplantıya gelen kişi.** Bu kişi yoksa pilot yok.
- [ ] **Ücret.** Küçük de olsa ücretli olsun. Bedava pilotun içinde şampiyon
      olmaz, aciliyet olmaz, IT sırayı bize vermez. Sabit ücretli, tanımlı
      çıktılı bir iş olarak sat.
- [ ] **Kapsam dışı olanlar.** Yazılı. Örnek: CRM entegrasyonu, SMS gönderimi,
      mobil uygulama, birden fazla şube. Yoksa kapsam kayması başlar.

Şablon: [Ek B — Pilot Mutabakat Formu](#ek-b--pilot-mutabakat-formu).

---

## Faz 0 — Geriye dönük test (Hafta 1-2)

Amaç: **modelin bu müşterinin verisinde çalışıp çalışmadığını**, hiçbir şey
kurmadan öğrenmek.

### 0.1 — Veri talebi (Gün 1)

[Ek A](#ek-a--veri-talep-e-postası)'daki e-postayı gönder. Kritik nokta:
**isim, telefon, e-posta istemiyoruz.** Müşterinin kendi iç `ogrenci_id`'si
yeterli; eşleştirmeyi onlar yapar. Bu hem KVKK yükünü ciddi azaltıyor hem de
satın alma tarafında "veri paylaşımı" direncini büyük ölçüde kaldırıyor.

İstenen: **son 12-18 ay**, öğrenci başına **aylık ya da haftalık satırlar**
(tek fotoğraf değil — trend özellikleri buna bağlı), ve **ayrılma tarihi**.

Kolon listesi: [Ek C](#ek-c--i̇stenecek-kolonlar).

### 0.2 — Veriyi teslim al (Gün 2-3)

- [ ] Şifreli kanal: paylaşılan bir Drive klasörü ya da şifreli zip. WhatsApp'tan
      veri dosyası **alma**.
- [ ] Tek bir yerde tut: `~/eoai-pilots/<musteri>/raw/`. Repoya **koymayacaksın**
      — `.gitignore` zaten `data/*.csv`'yi commit'liyor, oraya müşteri verisi
      düşerse her imaja ve her klona girer.
- [ ] Teslim tarihini ve dosya hash'ini not et (`shasum -a 256 dosya.csv`).
      Sonra "hangi veriyle eğitmişsiniz" sorusunun tek cevabı bu.

### 0.3 — Veri kalite kontrolü (Gün 3-4)

Model kurmadan önce bu beş soruyu cevapla. Cevaplardan biri kötüyse **hemen**
müşteriye dön; iki hafta sonra dönmek çok daha pahalı.

| Kontrol | Geçme ölçüsü | Geçmezse |
|---|---|---|
| Satır sayısı | ≥ 1.000 öğrenci, ≥ 150 ayrılan | Daha uzun geçmiş iste ya da pilotu "keşif" olarak yeniden çerçevele |
| Ayrılma oranı | %5-40 arası | %2 altı: sınıf dengesizliği ciddi, eşik ve metrikleri baştan konuş |
| Zaman boyutu | Öğrenci başına ≥ 6 periyot | Tek fotoğraf geldiyse trend özelliği kuramayız; sadece statik model olur, bunu söyle |
| Eksik veri | Kritik kolonlarda < %30 null | Kolonu düşür ya da müşteriye "bu alan neden boş" sor — genelde süreç sorunudur ve kendisi için de bilgi |
| Etiket tutarlılığı | Ayrılma tarihi ile son aktivite tarihi mantıklı | Uyumsuzluk varsa etiket tanımı yanlış — 0.4'e geçme |

### 0.4 — Etiket tanımını müşteriyle birlikte yaz (Gün 4)

**Bu adımı atlamak en pahalı hata.** "Ayrılma" onların sisteminde ne demek?

- Aboneliği iptal etmek mi, yenilememek mi, 60 gün hiç giriş yapmamak mı?
- Dönem sonunda doğal biten kayıt "churn" mü? (Genelde değil — mezun.)
- Dondurma / ara verme ne sayılıyor?

Ve **pencere**: kaç gün öncesinden haber vermek işe yarar? Mentorun müdahale
edebilmesi için 14 gün mü, 30 mu? Bu sayı hem etiketi hem ürünün değerini
tanımlıyor.

Yazılı tek cümleye indir ve müşteriye onaylat. Örnek:

> *Churn = öğrencinin 30 gün boyunca hiç ders/oturum kaydı olmaması, ve bu
> durumun devam ederek aboneliğin yenilenmemesiyle sonuçlanması. Erken uyarı
> penceresi: 21 gün.*

### 0.5 — Sızıntı kontrolü (Gün 4-5)

Denetimde çıkan en önemli teknik risk burada tekrar karşımıza gelir:
**bazı kolonlar churn'ün sonucu olabilir, nedeni değil.**

Her kolon için tek soru: *bu değer, churn penceresinin **başlamasından önce**
biliniyor muydu?*

- "Son iletişimden geçen gün" — öğrenci zaten kopmuşsa mentor aramayı bırakmıştır.
  Bu kolonu **pencere başlangıcından önceki** değerle hesapla, sonrasıyla değil.
- "İptal talebi kaydı", "iade işlemi", "hesap kapatma" — bunlar churn'ün
  kendisidir, feature değil. Çıkar.
- Fatura/ödeme durumu — ayrılan öğrencinin son faturası ödenmemiş olabilir.
  Tarihine bak.

Şüphelendiğin her kolonu **gecikmeli (lagged)** kur. Bir kolon sızıntı içeriyorsa
backtest harika görünür ve canlıda çöker; bunu Faz 1'de değil şimdi bulmak
istiyorsun.

### 0.6 — Sistemi müşterinin verisine yönelt (Gün 5-8)

Değiştirilecek yerler — README'nin "Onboarding a new client" bölümündeki 12
maddelik liste. Özet:

1. `config.py` — `RAW_DATA_PATH`, `FEATURES`, `CAT_COLS`, `STUDENT_INFO`,
   `TARGET_FEATURE`, `FEATURE_BOUNDS`, `FEATURE_LABELS`, `PLAN_MONTHS`
2. `src/data/features.py` — missing-flag'ler, impute grubu, türetilmiş kolonlar
3. `DECISION_COST` ve `PRECISION_AT_K` — [0.7](#07--eşiği-kapasiteye-bağla)'ye bak
4. `src/data/loader.py` + `db/schema.sql` — entity kolonu `student_id` değilse
5. `src/notifications/message.py` — Türkçe metinler müşterinin diline/tonuna göre

`config.py` import anında `_validate_feature_config()` çalıştırıyor; ne
unuttuğunu en hızlı o söyler.

### 0.7 — Eşiği kapasiteye bağla

Denetimde çıkan çelişki: eşik istatistiksel maliyetle seçiliyor ve öğrencilerin
%36'sını işaretliyor, ama `PRECISION_AT_K = 20` "mentor 20 kişi arayabilir"
diyor. Müşteride bunu baştan doğru kur:

- Kaç mentor var, her biri günde/haftada kaç öğrenciyle konuşabiliyor?
  → gerçek kapasite = `N`
- `PRECISION_AT_K = N` yap.
- `DECISION_COST`'u müşteriyle konuş: kaybedilen bir öğrencinin değeri (aylık
  ücret × ortalama kalan süre) vs bir yanlış alarmın maliyeti (mentorun 5-10
  dakikası). Bizim sentetik veride 3:1 kullanıyoruz; gerçekte bu oran çok daha
  yüksek çıkıyor ve eşiği aşağı çekiyor.
- Müşteriye gösterilecek sayı, eşikten çok **precision@N**: *"listenin ilk 20
  kişisinden 15'i gerçekten ayrılıyor."* İş tarafının anladığı dil bu.

### 0.8 — Zamana göre split ve backtest (Gün 8-11)

**Rastgele split kullanma.** Erken uyarı ürününde holdout tarihe göre olmalı:

```
eğitim:  ilk 12 ay
test:    son 3-6 ay
```

Böylece ölçtüğün şey "gelecek dönemin öğrencilerinde tutuyor mu" oluyor;
"aynı kohortun diğer öğrencilerinde tutuyor mu" değil.

Ürettiğin rapor **tek bir cümleye** indirgenmeli:

> *"Son 4 ayda ayrılan 312 öğrencinin 197'sini (%63), ayrılmalarından ortalama
> 24 gün önce, günlük ilk 40 kişilik listede yakalıyorduk. Aynı dönemde
> mentorların rastgele bir 40 kişilik listesi 51 öğrenci yakalardı."*

Son cümle kritik: **karşılaştırma tabanı olmadan sayının anlamı yok.** Baseline
olarak ya rastgele seçim, ya müşterinin bugün kullandığı yöntem (varsa),
ya da tek kural ("60 gündür girmeyenler").

### 0.9 — Faz 0 raporu (Gün 11-14)

4-6 sayfa, müşterinin okuyacağı dilde. İçermesi gerekenler:

1. **Tek cümlelik sonuç** (yukarıdaki gibi) ve baseline karşılaştırması
2. precision@N / recall / kaç gün önceden yakalandığı
3. En güçlü 5 sinyal ve iş tarafının anlayacağı yorumu — *"anketi
   doldurmayanlar, dolduranlara göre 2.3 kat daha sık ayrılıyor"*
4. Veride bulduğumuz sorunlar (dürüstçe) — eksik alanlar, tutarsızlıklar. Bu
   bölüm çoğu zaman müşteri için raporun en değerli kısmı
5. Bir örnek gün: o günün listesi ve mesajı, gerçek verilerinden
6. Faz 1 için ne gerektiği

### 🚪 Kapı 0 → 1

| Sonuç | Karar |
|---|---|
| precision@N, baseline'ın belirgin üstünde | Faz 1'e geç |
| Baseline'a yakın | **Dur.** Veriyi veya etiketi tartış, pilotu uzatma |
| Backtest çok iyi (ör. %95) | **Şüphelen.** Neredeyse kesin sızıntı var — 0.5'e dön |

Kapı geçilmezse bu bir başarısızlık değil: 2 haftada öğrendik, 4 ayda değil. Ve
elde müşterinin veri kalitesi hakkında gerçek bir rapor var — o bile satılabilir
bir çıktı.

---

## Faz 1 — Gölge mod (Hafta 3-8)

Sistem her gün çalışıyor, alarmlar üretiliyor, **kimse aksiyon almıyor.**
Alarmlar sadece bize ve müşteri tarafındaki sahibe gidiyor.

Amacı iki: teknik güvenilirliği kanıtlamak, ve modelin canlı veride de
backtest'e yakın davrandığını görmek.

### 1.1 — Teknik blokerler (Faz 1'e girmeden kapatılmalı)

Bunlar denetimde çıkan ve **gerçek müşteri verisi** dokunduğu an kabul edilemez
hale gelen maddeler. Her biri için repoda issue açılır.

| # | İş | Neden |
|---|---|---|
| P1 | `API_KEY` fail-closed + dashboard için header ekleyen ters proxy | Şu an boş anahtar = kimlik doğrulama tamamen kapalı. Reşit olmayan öğrenci verisi |
| P2 | `POST /run-daily-pipeline`'ı HTTP yüzeyinden kaldır | Korumasız yazma endpoint'i; her çağrı `new` etiketlerini siliyor, sabah mesajı "0 yeni" çıkıyor |
| P3 | Otomatik `pg_dump` (günlük, 30 gün saklama) | `alerts` hiçbir yerden geri gelmiyor; ürünün tüm değer kanıtı orada |
| P4 | `runs` tablosu: `run_id`, `started_at`, `model_version`, `threshold`, `student_count`, `at_risk_count`, `status` | Koşu kimliği yok; boş koşu hiç yazılmıyor, yani "kimse riskli değil" ile "sistem 3 gün ölü" ayırt edilemiyor |
| P5 | `alerts.features` (JSONB snapshot) + `model_version` + `threshold` | "3 Eylül'de bunu neden işaretledin" sorusunun cevabı; ve sonuç analizinin ön koşulu |
| P6 | `/metrics` allow-list | Şu an developer path'leri, imputation medyanları ve demografik hata analizi dönüyor |
| P7 | `.dockerignore`'a `data/`, `RnD/`, `notebooks/` | Yoksa müşteri verisi her imaja gömülüyor |
| P8 | `scripts/forget_student.py` — silme/anonimleştirme | KVKK silme talebi geldiğinde; FK şu an engelliyor |
| P9 | `daily_students`'a `as_of_date` veya `is_active` | Yoksa programı bitiren öğrenciler sonsuza kadar aranıyor |
| P10 | CI'a Postgres servisi | 9 DB testi her PR'da sessizce atlanıyor; şema hatası müşteride ortaya çıkıyor |

P1-P5 pazarlıksız. P6-P10 Faz 1 içinde kapanabilir.

### 1.2 — Nerede çalışacak

| Seçenek | Ne zaman | Notlar |
|---|---|---|
| **Müşterinin kendi sunucusu** | Tercih edilen. "Veri sizden çıkmıyor" cümlesinin karşılığı | Docker gerekli; IT ile 1 toplantı. `demo_up.sh` yerine `docker compose up -d` + cron |
| **Bizim yönettiğimiz VPS (TR bölgesi)** | Müşterinin sunucusu yoksa / IT yavaşsa | Veri işleyen sözleşmesi şart. Hetzner/DigitalOcean TR ya da AB. HTTPS + basic auth + IP kısıtı |
| **Sadece bizim makinemizde, manuel** | Yapmayın | Günlük koşu insana bağlı olur, ilk tatilde biter |

Her iki gerçek seçenekte de: cron ile 09:00, `&&` ile zincirli (koşu başarısızsa
mesaj gitmesin), ve koşu başarısız olursa **bize** bildirim.

### 1.3 — Veri akışı

Müşteri günlük veriyi nasıl verecek? Karmaşıklık sırasıyla:

1. **Zamanlanmış CSV dump** — onların sisteminden bir SFTP/Drive klasörüne
   günlük dosya. En basit, en dayanıklı, ilk pilotta bunu seç.
2. **Read-only DB kullanıcısı** — kendi veritabanlarına salt-okunur erişim.
   Daha temiz ama IT onayı uzun sürer.
3. **API entegrasyonu** — sadece onların hazır API'si varsa.

Ne seçilirse, `scripts/load_daily_students.py` girişi olarak bir CSV bekliyor;
1 ve 2 için araya 20 satırlık bir çekme scripti yeterli.

### 1.4 — Günlük izleme

- [ ] Koşu logu ve `runs` tablosu her sabah kontrol edilir (P4 bunun için)
- [ ] Alarm sayısı ani değişirse incele: veri akışı bozulmuş olabilir
- [ ] Haftalık: canlı precision@N'i backtest ile karşılaştır. Ciddi sapma =
      veri kayması ya da sızıntı

### 1.5 — İki yeni tablo: sonuç ve müdahale

**Bu adım pilotun ticari başarısını belirliyor** ve teknik blokerlerden daha
önemli. Denetimde çıkmadı çünkü kod eksikliği değil, ürün eksikliği.

**a) Sonuç takibi (label capture).** Alarm verdiğimiz öğrenci gerçekten ayrıldı
mı? Kaydedilmezse ikinci ay modeli iyileştiremeyiz — veri çarkı buradan başlıyor.
Müşteriden aylık bir "ayrılanlar" listesi yeter.

**b) Müdahale kaydı.** Mentor ne yaptı — aradı mı, ne zaman, ne konuştu, sonuç?
Bu olmadan *"sistem sayesinde N öğrenci kaldı"* **hiçbir zaman**
kanıtlanamaz; sadece korelasyon olur. Ve müşterinin yenileme kararı tam bu
sayıya bakacak.

En basit hali: dashboard'daki "iletişime geçildi" işaretinin kalıcı hale
getirilmesi + bir not alanı. Şu an component state'te tutuluyor, sayfa
yenilenince sıfırlanıyor.

### 🚪 Kapı 1 → 2

- [ ] 4 hafta kesintisiz koşu (kaçırılan gün sayısı ≤ 1)
- [ ] Canlı precision@N, backtest'in makul aralığında
- [ ] Müşteri tarafındaki sahip alarmları okuyor ve "bu isimler mantıklı" diyor
- [ ] Sonuç ve müdahale kaydı çalışıyor

Üçüncü madde sayısal değil ama en önemlisi: mentorlar listeye bakıp "bunlar
gerçekten riskli öğrenciler" demiyorsa, metrik ne derse desin ürün
benimsenmeyecek.

---

## Faz 2 — Kontrollü müdahale (Hafta 9-16)

Bir mentor ekibi listeye göre hareket ediyor, diğerleri eskisi gibi çalışıyor.
Yani **kontrol grubu var.** "İşe yarıyor mu" sorusunun tek dürüst cevabı bu.

### 2.1 — Mentor eğitimi (30 dakika)

Anlatılacaklar, bu sırayla:

1. Bu bir "kesin ayrılacak" listesi **değil**, "önce bunlarla konuş" listesi
2. Gerekçeler neden var: ne konuşacağını bilmek için
3. Model yanılır — yanıldığında söyle, bu bizim için veri
4. "İletişime geçildi" işaretini bırakmak **zorunlu**, ölçüm ona bağlı

Anlatılmayacak: eşik, SHAP, kalibrasyon. Kimse ilgilenmiyor ve güveni azaltıyor.

### 2.2 — Kontrol grubu kurgusu

- Mentorları değil, **öğrencileri** rastgele böl (mentor bazlı bölmek mentor
  kalitesini ölçer, ürünü değil)
- 50/50 en temizi. Müşteri direnirse 70/30 (müdahale/kontrol)
- Süre boyunca grup değiştirme yok
- Her iki grup da aynı şekilde skorlanıyor; sadece müdahale grubunun listesi
  mentora gidiyor

### 2.3 — Haftalık ritüel (30 dk, sabit gün)

1. Geçen haftanın listesi ve ne yapıldığı
2. İki gruptaki ayrılma sayıları
3. Mentorlardan gelen "bu yanlıştı" örnekleri
4. Bir sonraki hafta ne değişiyor

[Ek E](#ek-e--haftalık-durum-raporu-şablonu)'deki şablonu kullan. Haftalık
rapor, yenileme konuşmasının delil dosyası olur.

### 2.4 — Ölçüm

Ana metrik: **iki gruptaki tutulma oranı farkı.** İkincil: yakalama oranı,
ortalama uyarı süresi, mentor başına müdahale sayısı, mentorların listeyi
kullanma oranı.

Dürüstlük notu: 8 hafta ve tek şube ile istatistiksel anlamlılık genelde
yakalanmaz. Bunu **baştan** söyle: *"Bu pilot bir yön gösterir, kesin bir etki
büyüklüğü vermez."* Sonradan itiraf etmekten iyidir.

### 🚪 Kapı 2 → 3

Müdahale grubunda tutulma, kontrol grubundan iyi mi? Ve müşteri, mentorlarının
bu listeyi kullanmaya devam etmesini istiyor mu? İkinci soru birincisinden daha
belirleyici.

---

## Faz 3 — Karar

### Devam ederse

- Aylık/yıllık abonelik. Fiyat çıpası: kurtarılan öğrencinin değeri.
  *"3 öğrenci kurtarırsanız sistem kendini ödüyor"* — hesabını onların
  sayılarıyla yap.
- Yaygınlaştırma planı: diğer şubeler/ekipler
- Devir dokümanı: kim neyi işletiyor, bir şey bozulduğunda kim aranıyor
- Referans ve vaka çalışması izni **iste** (en değerli çıktı, çoğu kurucu
  istemeyi atlar)

### Devam etmezse

- Veriyi sözleşmede yazdığı gibi sil, silindiğini yazılı bildir
- Neden olmadığını kendi içimizde yaz: veri mi, model mi, benimseme mi, fiyat mı?
- Öğrenilenleri bir sonraki müşteriye taşı ve ürüne yaz

---

# Ekler

## Ek A — Veri talep e-postası

> **Konu:** EO-Churn pilotu — veri talebi
>
> Merhaba <isim>,
>
> Pilotun ilk adımı olan geriye dönük analiz için aşağıdaki veriye ihtiyacımız
> var. Amacımız şu soruyu cevaplamak: bu sistem son bir yıl boyunca çalışıyor
> olsaydı, ayrılan öğrencilerin kaçını önceden yakalardı?
>
> **İstediğimiz:** son 12-18 ay, öğrenci başına aylık (mümkünse haftalık) bir
> satır olacak şekilde tek bir CSV.
>
> **İstemediğimiz — lütfen göndermeyin:** isim, soyisim, telefon, e-posta, TC
> kimlik numarası, adres. Sizin kendi iç öğrenci numaranız yeterli;
> eşleştirmeyi siz yapacaksınız. Bu, hem KVKK açısından en doğru yol hem de
> süreci hızlandırıyor.
>
> Kolonlar ekte. Hepsi olmak zorunda değil — elinizde olanları gönderin, eksik
> olanları birlikte değerlendiririz. En kritik olan **ayrılma tarihi**; o
> olmadan analiz yapılamıyor.
>
> Dosyayı paylaşılan klasöre koyabilir ya da şifreli zip olarak
> gönderebilirsiniz. Aldıktan sonra 3 iş günü içinde veri kalitesi
> değerlendirmesini paylaşırız.
>
> Teşekkürler,
> Rıza

## Ek B — Pilot Mutabakat Formu

```
MÜŞTERİ: ..............................
PİLOT BAŞLANGIÇ: ......../......../..........
PİLOT BİTİŞ:     ......../......../..........

BAŞARI KRİTERİ (sayı olarak)
  Günlük ....... kişilik listede, ayrılan öğrencilerin en az %.......'i,
  ayrılmadan en az ....... gün önce yer alacak.
  Karşılaştırma tabanı: ........................................

SORUMLULAR
  Müşteri tarafı sahibi (haftalık toplantıya gelen): ..................
  Veri sahibi:                                       ..................
  EOAI Lab sorumlusu:                                ..................

VERİ
  İlk teslim tarihi:     ......../......../..........
  Kapsam:                son ....... ay, ....... periyodik
  Kişisel veri:          isim/telefon/e-posta/TC PAYLAŞILMAYACAK
  Saklama süresi:        pilot bitiminden ....... gün sonra silinir

KURULUM YERİ
  [ ] Müşterinin sunucusu   [ ] EOAI Lab yönetiminde VPS (bölge: ........)

ÜCRET
  Pilot ücreti: ....................  Ödeme: ....................
  Sonrası abonelik aralığı (gösterge): ....................

KAPSAM DIŞI (yazılı)
  ....................................................................
  ....................................................................

HAFTALIK TOPLANTI: ............... günü, ....... saat

İmzalar: ....................        ....................
```

## Ek C — İstenecek kolonlar

**Zorunlu**

| Kolon | Açıklama |
|---|---|
| `ogrenci_id` | müşterinin kendi iç id'si |
| `donem` / `as_of_date` | satırın hangi aya/haftaya ait olduğu |
| `kayit_tarihi` | programa başlangıç |
| `ayrilma_tarihi` | ayrıldıysa; boş = devam ediyor |

**Yüksek değerli davranış kolonları** (hangisi varsa)

| Kolon | Neden |
|---|---|
| oturum/ders sayısı (periyot içinde) | temel katılım sinyali |
| platformda geçirilen süre | katılım yoğunluğu |
| son giriş tarihi | dikkat: churn'ün sonucu olabilir, gecikmeli kur |
| ödev/quiz tamamlama oranı | program uyumu |
| sınav/deneme sonuçları ve **trendi** | sonuç algısı — genelde en güçlü sinyallerden |
| mentor/öğretmen görüşme sayısı | ⚠️ ters nedensellik riski, gecikmeli kur |
| mesajlara yanıt süresi | ilgi düzeyi |
| destek talebi / şikâyet sayısı | memnuniyet vekili |
| ödeme gecikmesi (gün) | dikkat: ayrılma kararının sonucu olabilir |
| memnuniyet anketi puanı ve **doldurup doldurmadığı** | doldurmamak da sinyal |

**Bağlam** (statik)

sınıf/seviye, program/paket türü, ödeme planı, şube/şehir, kanal (nereden geldi),
varsa indirim/kampanya bilgisi.

**İstemediklerimiz:** isim, telefon, e-posta, TC kimlik, adres, veli iletişim
bilgisi, sağlık bilgisi, notlarda geçen serbest metin.

## Ek D — KVKK kontrol listesi

- [ ] Veri işleyen sözleşmesi imzalı (biz işleyen, müşteri veri sorumlusu)
- [ ] Yalnızca takma adlı (pseudonymized) veri alınıyor — isim/iletişim yok
- [ ] Veri minimizasyonu: modelin kullanmadığı kolonu istemiyoruz
- [ ] Saklama süresi yazılı, pilot sonu silme prosedürü tanımlı
- [ ] Silme teknik olarak mümkün (P8 — `forget_student.py`)
- [ ] Barındırma bölgesi yazılı (TR ya da AB)
- [ ] Erişimi olan kişiler listesi; ekipten kimler, neden
- [ ] Yedekler de şifreli ve aynı saklama süresine tabi
- [ ] **Reşit olmayan öğrenci** verisi işlendiği not edilmiş; veli rızası
      sorumluluğunun müşteride olduğu sözleşmede yazılı
- [ ] Alt işleyen varsa (VPS sağlayıcısı) müşteriye bildirilmiş

## Ek E — Haftalık durum raporu şablonu

```
EO-CHURN PİLOT — HAFTA ....... (........ - ........)

DURUM: 🟢 yolunda / 🟡 dikkat / 🔴 engelli

BU HAFTA
  Koşan gün sayısı:            ....... / .......
  Uyarılan öğrenci (toplam):   .......
  Yeni uyarı:                  .......
  İletişime geçilen:           .......
  Bu hafta ayrılan:            ....... (uyarı listemizde olan: .......)

ÖLÇÜM (kümülatif)
  Yakalama oranı (ilk N liste): .......%
  Ortalama uyarı süresi:        ....... gün
  Müdahale grubu tutulma:       .......%
  Kontrol grubu tutulma:        .......%

MENTORLARDAN GELEN
  ....................................................................

ENGELLER / KARAR BEKLEYENLER
  ....................................................................

ÖNÜMÜZDEKİ HAFTA
  ....................................................................
```

## Ek F — Pilotu öldüren yedi şey

| Ölüm sebebi | Panzehir |
|---|---|
| Veri gelmiyor | Faz -1'de tarih + isim yaz; 1 hafta gecikmede sponsoru bilgilendir |
| Müşteri tarafında sahip yok | İmzadan önce şart koş; haftalık toplantıya 2 kez gelmezse pilotu durdur |
| Başarı kriteri muğlak | Sayıyla yaz, imzala |
| Kapsam kayması ("bir de şunu ekleyebilir miyiz") | Kapsam dışı listesi yazılı; her ek talep Faz 3 konusu |
| Mentorlar listeyi kullanmıyor | Faz 2'de kullanım oranını da ölç; %50 altındaysa bu bir ürün sorunu, model sorunu değil |
| Model zayıf çıkıyor | Faz 0'da öğrenilir; kapıyı zorlama, veriyi tartış |
| Sessiz teknik arıza | P4 (`runs` tablosu) + koşu başarısızlığında bize bildirim |

## Ek G — Faz 1 öncesi açılacak issue'lar

Repoda tek tek issue olarak açılır, `pilot-blocker` etiketiyle:

```
P1  auth: API_KEY fail-closed + dashboard icin ters proxy
P2  api: POST /run-daily-pipeline'i HTTP yuzeyinden kaldir
P3  ops: gunluk pg_dump + 30 gun saklama
P4  db: runs tablosu (kosu kimligi, bos kosu kaydi, model_version, threshold)
P5  db: alerts.features snapshot + model_version + threshold kolonlari
P6  api: /metrics allow-list
P7  docker: .dockerignore'a data/, RnD/, notebooks/
P8  kvkk: scripts/forget_student.py (silme + anonimlestirme)
P9  db: daily_students'a as_of_date / is_active, pasif ogrenciyi skorlamayi bırak
P10 ci: Postgres servisi ekle, DB testleri gercekten kossun
```

---

*Bu dosya pilotlar sırasında güncellenir. Her pilottan sonra "ne öğrendik"
bölümü eklenir — ikinci pilot birincinin hatalarını tekrarlamasın.*
