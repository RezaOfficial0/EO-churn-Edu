# Geriye dönük analiz için veri talebi

Son 12 ayın verisinden, **ayrılan öğrencilerinizin hangilerinin önceden
işaretlenebileceğini ve ne kadar önce işaretlenebileceğini** çıkarıyoruz.

- Sistemimize bağlanmıyoruz, kurulum yapmıyoruz, sisteminize erişmiyoruz.
- Siz iki CSV dosyası gönderiyorsunuz, biz analizi geri gönderiyoruz.
- Kolon adlarınız bizimkiyle aynı olmak zorunda değil: dosyaların **başlık
  satırını** da iletin, eşlemeyi biz yaparız.
- **Kişisel veri göndermeniz gerekmiyor ve göndermemenizi istiyoruz** (aşağıda
  tam liste var).
- Süre: dosyalar elimize geçtikten sonra iki hafta.

Mühendisiniz bu sayfayla, bize tekrar sormadan dosyaları üretebilir. Bir şey
belirsiz kalırsa o kolonu boş bırakın — boş kolon, yanlış kolondan iyidir.

---

## Dosya 1 — `ogrenciler.csv`

**Her öğrenci için, her ay bir satır.** Yani bir öğrenci 12 ay kayıtlıysa 12
satır. Her satır, o ayın başında sistemde ne göründüğünü taşır.

Bu "her ay bir satır" kısmı tek gerçek zorunluluk: "ne kadar önce" sorusunun
cevabı buradan çıkıyor. Tek bir güncel kesit gönderirseniz de çalışırız, ama o
soruyu cevaplayamayız.

### Zorunlu olan sadece iki kolon

| kolon | ne |
|---|---|
| `student_id` | **takma kimlik** — hash'lenmiş ya da sizin sıra numaranız. İki dosyada aynı olmalı |
| `as_of_date` | satırın ait olduğu an, `2026-03-01` biçiminde |

Bu ikisi olmadan analiz kurulamaz: biri kimi, diğeri ne zaman gördüğümüzü söylüyor.
Geri kalan her kolon analizi **iyileştirir**, hiçbiri şart değildir. Bir kolonu
veremiyorsanız boş bırakın ya da hiç göndermeyin — çalışırız, sadece o sinyali
kullanamayız.

### Varsa gönderin: öğrencinin kim olduğu

Bunlar sonucu segmentlere ayırmamızı sağlıyor — "hangi sınıfta, hangi pakette
daha çok kaybediyorsunuz" sorusunu ancak bunlarla cevaplayabiliyoruz.

| kolon | ne |
|---|---|
| `grade` | sınıf: `11. Sınıf`, `12. Sınıf`, `Mezun` — sizdeki karşılıkları ne ise |
| `track` | alan: `Sayısal`, `Eşit Ağırlık`, `Sözel`, `Dil` |
| `city_tier` | şehir kademesi ya da sadece şehir adı. **Adres göndermeyin** |
| `plan_type` | paket: `Aylık`, `3 Aylık`, `Yıllık` |
| `monthly_fee_try` | paketin **toplam** fiyatı (aylık tutar değil) |
| `tenure_months` | öğrencinin programdaki süresi, ay |

`monthly_fee_try` ve `plan_type` birlikte gelirse raporda parasal büyüklüğü de
hesaplayabiliyoruz; gelmezse rapor o bölüm olmadan çıkar.

### Varsa gönderin: öğrencinin davranışı

**Asıl sinyal bunlarda.** Hepsini beklemiyoruz; elinizde ne varsa. En az üç tanesi
gelmezse model eğitilemez — tek gerçek alt sınır bu.

| kolon | ne |
|---|---|
| `program_adherence_rate` | program uyum oranı, 0–1 |
| `weekly_study_hours_planned` | o hafta için planlanan çalışma saati |
| `weekly_study_hours_actual` | gerçekleşen çalışma saati |
| `message_response_time_hours` | **öğrencinin** mesajlara yanıt süresi, saat |
| `late_response_count_30d` | son 30 günde geç yanıt sayısı |
| `trial_exam_count_total` | girdiği toplam deneme sınavı |
| `trial_exam_avg_net` | deneme sınavı net ortalaması |
| `trial_exam_score_trend` | netlerin eğilimi (artı = yükseliyor) |
| `missed_trial_exam_count` | kaçırdığı deneme sınavı sayısı |
| `payment_delay_days_avg` | ortalama ödeme gecikmesi, gün |
| `support_ticket_count_90d` | son 90 günde açtığı destek talebi |
| `satisfaction_survey_score` | son memnuniyet anketi puanı, 1–5 |
| `parent_involvement` | veli ilgisi: `Düşük`, `Orta`, `Yüksek` |

**Mentor tarafındaki kolonları göndermeyin** — "son görüşmeden bu yana kaç gün",
"aylık mentor görüşme sayısı" gibi. Bunlar mentorun davranışını ölçüyor,
öğrencinin davranışını değil, ve analizi sahte biçimde güzelleştiriyor. Bu kararın
sayılarla gerekçesi elimizde yazılı, isterseniz paylaşırız.

### Örnek

```csv
student_id,as_of_date,grade,track,city_tier,plan_type,monthly_fee_try,tenure_months,program_adherence_rate,weekly_study_hours_planned,weekly_study_hours_actual,message_response_time_hours,late_response_count_30d,trial_exam_count_total,trial_exam_avg_net,trial_exam_score_trend,missed_trial_exam_count,payment_delay_days_avg,support_ticket_count_90d,satisfaction_survey_score,parent_involvement
A0417,2026-01-01,12. Sınıf,Sayısal,Tier 1 (Büyükşehir),Yıllık,16730.00,8.0,0.81,14.0,11.5,9.4,1,22,58.4,1.20,0,0.0,1,4.2,Yüksek
A0417,2026-02-01,12. Sınıf,Sayısal,Tier 1 (Büyükşehir),Yıllık,16730.00,9.0,0.74,14.0,8.0,19.8,3,24,55.1,-0.80,1,0.0,2,3.4,Yüksek
A0417,2026-03-01,12. Sınıf,Sayısal,Tier 1 (Büyükşehir),Yıllık,16730.00,10.0,0.58,14.0,4.5,41.2,6,24,52.7,-2.40,3,4.0,3,,Yüksek
A1182,2026-03-01,11. Sınıf,Eşit Ağırlık,Tier 2,Aylık,1800.00,2.0,0.88,12.0,12.5,6.1,0,3,47.9,0.60,0,0.0,0,4.5,Orta
```

Son satırdaki boş `satisfaction_survey_score` doğru bir gönderimdir: anket
doldurulmadıysa boş bırakın, `0` yazmayın. Boş olmasının kendisi bir bilgi.

---

## Dosya 2 — `ayrilmalar.csv`

**Programdan ayrılan her öğrenci için bir satır.** Ayrılmayan öğrenciler bu
dosyada olmaz.

| kolon | ne |
|---|---|
| `student_id` | dosya 1'deki **aynı** takma kimlik |
| `ayrilma_tarihi` | `2026-04-18` biçiminde |
| `ayrilma_turu` | varsa: `iptal`, `yenilemedi`, `devamsiz`, `transfer` … |

```csv
student_id,ayrilma_tarihi,ayrilma_turu
A0417,2026-04-18,iptal
A0931,2026-02-03,yenilemedi
A1455,2026-05-27,devamsiz
```

### Bir cümle de yazın: "ayrılma" sizde ne demek?

Bu dosyadan daha önemli. "Ödemeyi kesti", "programı bıraktı", "iki ay giriş
yapmadı", "sözleşmeyi yenilemedi" — dördü farklı sayılar üretir. Hangisini
kullandığınızı tek cümleyle yazın; analizde sizin tanımınızı kullanırız, kendi
tanımımızı dayatmayız.

---

## Göndermemeniz gerekenler

Bunlar bize hiç ulaşmasın. Analiz için gerekmiyorlar; gelirlerse dosyayı
işlemeden iade ediyoruz.

**Hiçbir koşulda:** ad, soyad, telefon, e-posta, T.C. kimlik numarası, okul
numarası, veli adı ve iletişimi, adres, doğum tarihi, fotoğraf, öğretmen/mentor
adı, serbest metin notlar.

**`student_id` yerine ne gelmeli:** kendi kimliklerinizi bir yöne doğru
dönüştürülmüş bir değerle (hash) ya da basit bir sıra numarasıyla değiştirin.
Tek şart: **iki dosyada aynı olmalı** ve 12 ay boyunca aynı öğrenci için aynı
kalmalı. Eşlemeyi siz tutun, bize göndermeyin.

**`city_tier`** şehir kademesi olarak gelsin; ilçe, mahalle, posta kodu
gelmesin. Şehir adı da yeterlidir.

**Doğum tarihi yerine** `grade` zaten elimizde — yaşa ihtiyacımız yok.

Bu kadarı zaten dosyayı büyük ölçüde kişisel veri olmaktan çıkarıyor. Yine de
tarafımızda: dosya ayrı bir dizinde tutuluyor, versiyon kontrolüne ve hiçbir
kurulum imajına girmiyor, log'larımıza öğrenci kimliği yazılmıyor.

---

## Format

Mühendisinizin bir daha sormaması için:

| | |
|---|---|
| kodlama | UTF-8 (Excel'den "CSV UTF-8" olarak kaydedin) |
| ayraç | virgül `,` tercih edilir; noktalı virgül `;` de olur — hangisini kullandığınızı yazın |
| ondalık | nokta `.` tercih edilir; virgül `,` da olur — ayraçla karışmaması için ayracı belirtin |
| tarih | `YYYY-AA-GG`, ör. `2026-03-01` |
| eksik değer | **boş bırakın**. `0`, `-`, `yok`, `NULL` yazmayın — bunlar gerçek değerlerden ayırt edilemez |
| tekrar eden satır | aynı öğrenci + aynı `as_of_date` iki kez olmasın |
| dosya boyutu | sıkıştırılmış `.zip` gönderebilirsiniz |

Excel'den çıkan dosyada ondalıkların virgül, ayracın noktalı virgül olması
olağandır — düzeltmeniz gerekmiyor, sadece hangisi olduğunu söyleyin.

---

## Her kolon hangi dönemi topluyor?

Tek bir kısa cevap yeterli ve analizin doğruluğu buna bağlı. Örnek olarak:
`program_adherence_rate` "başlangıçtan bugüne kadarki oran" mı, "son 30 günün
oranı" mı? `trial_exam_score_trend` kaç sınavın eğilimi?

Bunu bilmezsek bir kolonun, tahmin etmesi gereken dönemin **içindeki** veriyi
taşıyıp taşımadığını anlayamıyoruz — o durumda analiz olduğundan iyi görünür ve
size yanlış bir sonuç göndermiş oluruz. Kolon adlarının yanına birer satır
yazmanız yeterli; emin olmadığınız kolonu "bilmiyorum" diye işaretleyin, o da
bir cevap.

---

## En az hâli: yarısını verebiliyorsanız

| elinizde olan | yapabildiğimiz | yapamadığımız |
|---|---|---|
| iki dosya, 12 aylık aylık satırlar, davranış kolonlarının çoğu | tam analiz: sıralama + "ne kadar önce" | — |
| aylık değil **çeyreklik** satırlar | aynı analiz, "ne kadar önce" çeyrek çözünürlükte | ay bazlı erkenlik |
| davranış kolonlarından sadece 3–5 tanesi | analiz, hangi sinyallerin ölçülemediği listesiyle | eksik kolonlara dair hiçbir şey |
| sadece **tek bir güncel kesit** (tarih yok ya da tek tarih) | **hiçbir şey** — analiz kurulamaz | aşağıya bakın |
| davranış kolonu üçten az | **hiçbir şey** — model eğitilemez | aşağıya bakın |
| `ayrilmalar.csv` yok, ama dosya 1'de bir `ayrildi` kolonu var | aynı analiz, ayrılma tarihi yoksa "ne kadar önce" düşer | — |

### İki kesin alt sınır

Bu ikisi karşılanmazsa analiz **koşmuyor** — esnetilebilir bir şey değil, yöntemin
kendisi buna dayanıyor:

- **En az 90 günlük geçmiş.** "Ne kadar önce" sorusu, geçmişte bir noktaya gidip
  o gün ne bileceğimizi hesaplayıp sonrasına bakarak cevaplanıyor. Tek bir güncel
  kesitte gidilecek bir geçmiş yok. 90 gün mutlak taban; 12 ay istememizin sebebi
  bu tabanın birkaç kez tekrarlanabilmesi.
- **En az 3 davranış kolonu.** Daha azıyla eğitilecek bir model yok.

Bir de şu ikisi olmadan ölçülecek bir şey kalmıyor: hangi öğrencilerin ayrıldığı,
ve ayrılma tanımınızın bir cümlesi.

Ayrılan öğrenci sayısı çok azsa — otuzun altı gibi — sayılar o kadar geniş bir
belirsizlik aralığıyla gelir ki tek bir orana dönüşmezler. O durumda analiz
yerine şunu gönderiyoruz: hangi sinyallerin verinizde ölçülebilir olduğu, hangi
kolonların eksik olduğu, ve bir sonuç çıkarmak için ne kadar veri gerektiği.
Bunu baştan söylüyoruz ki iki hafta sonra sürpriz olmasın.

---

## Sonra ne oluyor

1. Dosyalar elimize geçer, **aynı gün** bir uygunluk kontrolü yaparız. Analizi
   engelleyen bir şey varsa (eksik etiket, tanım belirsizliği, kişisel veri) o
   gün söyleriz — iki hafta bekletip sonra söylemeyiz.
2. İki hafta içinde bir sayfalık analiz + eki gönderir, 30 dakikalık bir okuma
   görüşmesi yaparız.
3. Raporda şunlar olur: ayrılanlarınızın kaçının önceden listede göründüğü,
   mentorunuzun bir günde arayabileceği kadar isim verildiğinde isabet oranı ve
   belirsizlik aralığı, kaçırdıklarımızın oranı, erkenlik medyanı, hangi
   kolonların kullanılamadığı ve neden.

**Raporda olmayacak şey:** "şu kadar öğrenci kurtarılırdı" ya da "şu kadar gelir
kurtarılırdı". Geçmişte kimse bu uyarılarla bir öğrenciyi aramadı; analiz
ayrılmanın verinizde **önceden görünür olduğunu** gösterir, müdahalenin işe
yaradığını göstermez. Onu ancak ileriye dönük bir pilot gösterir.

Raporda bir TL rakamı **var**: işaretlenen öğrencilerin aylık değerinin toplamı.
Bu "risk altına giren ve görünür hale gelen tutar"dır, kurtarılan tutar değil —
raporda da aynen böyle yazıyor.

---

## Veriye ne oluyor

**Rapor teslim edildiği gün dosyaları siliyoruz.** Belirsiz bir "gerekmedikçe
tutmuyoruz" değil: rapor elinize geçtiği gün gönderdiğiniz iki dosya ve
analiz sırasında üretilen ara dosyalar siliniyor.

Bu süre boyunca dosyalar:

- tek bir bilgisayarda duruyor, şifreli diskte
- hiçbir bulut hizmetine, hiçbir yedeklemeye, hiçbir kod deposuna girmiyor
- analizi yapan kişi dışında kimseye açılmıyor
- hiçbir model eğitiminde, hiçbir başka müşteri çalışmasında kullanılmıyor

Rapor da öğrenci bazlı satır içermiyor — toplamlar, oranlar ve segment
kırılımları var. Yani rapor elinizde kalsa bile içinde kimsenin verisi yok.

Pilot konuşmaya dönerse veriyi tekrar istiyoruz. Bunu bilerek böyle kurduk:
"belki sonra lazım olur" diye veri tutmak, tutulan verinin korunması sorumluluğunu
da getiriyor ve bu aşamada o sorumluluğu almıyoruz.

## Ücret

**Analiz ücretsizdir.** Karşılığında bir şey satın almış olmuyorsunuz, bir
taahhüde girmiyorsunuz. Bizim için değeri, sistemin gerçek veride ne yaptığını
görmek; sizin için değeri, mevcut durumunuzun ölçülmüş bir resmi.

## Sözleşme

Kurumunuzun gizlilik sözleşmesi varsa **imzalarız** — bize kendi metninizi
gönderin yeterli. Kendi taslağımızı dayatmıyoruz.

Veri anonimleştirilmiş geldiği ve kimlik bilgisi içermediği için çoğu kurumda
bu adım gerekmiyor; gerekiyorsa engel değil.

## Dosyaları nereye göndereceksiniz

**[İLETİŞİM — doldurulacak]**

Sorularınız için de aynı adres.
