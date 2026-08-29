# API Contract — Churn Early Warning System

Frontend icin. Kod okumaya gerek yok, sunucu ayaktayken `http://127.0.0.1:8000/docs`
adresinde interaktif Swagger UI de var (her endpoint'i orada da deneyebilirsiniz).

## Calistirma

```
pip install -r requirements.txt
uvicorn api.main:app --reload
```
Base URL: `http://127.0.0.1:8000`

CORS acik (MVP, `allow_origins=["*"]`) — farkli portta (React/Vite dev server vs.)
calisirken sorun cikarmaz.

## Hata formati

Her hata `4xx`/`5xx` status code + su govde ile doner:
```json
{"detail": "aciklayici mesaj"}
```

---

## GET /health
Saglik kontrolu.
```json
{"status": "ok"}
```

## POST /predict
Raw ogrenci verisi gonder, tek kisi icin churn tahmini al. Body'de config.py'daki
FEATURES listesindeki TUM alanlar zorunlu (24 alan). Eksik/yanlis tip alan varsa 422 doner.

Response:
```json
{
  "churn_probability": 0.4851,
  "top_reasons": [
    {"feature": "mentor_contact_freq_per_month", "impact": 0.28},
    {"feature": "days_since_last_contact", "impact": -0.27},
    {"feature": "program_adherence_rate", "impact": -0.07}
  ]
}
```
`impact` pozitifse churn riskini artiriyor, negatifse azaltiyor.

## GET /predict/{student_id}
Ogrenciyi guncel `daily_data.csv` icinden bulur, ayni sekilde tahmin doner.
```json
{
  "student_id": "STU300001",
  "enrollment_date": "2025-04-10",
  "churn_probability": 0.4851,
  "top_reasons": [ ... ]
}
```
Bulunamazsa 404: `{"detail": "student_id not found in daily data: ..."}`

## POST /run-daily-pipeline?threshold=0.5
Gunluk pipeline'i tetikler (tum data uzerinde predict + SHAP), threshold uzerindeki
riskli ogrencileri doner. `threshold` opsiyonel, default 0.5.
```json
{
  "churn_risk_count": 9,
  "students": [
    {
      "student_id": "STU300010",
      "enrollment_date": "2025-02-01",
      "churn_probability": 0.71,
      "top_reasons": "days_since_last_contact (+0.59), mentor_contact_freq_per_month (+0.28)",
      "top_reasons_detail": [
        {"feature": "days_since_last_contact", "impact": 0.59},
        {"feature": "mentor_contact_freq_per_month", "impact": 0.28}
      ]
    }
  ]
}
```
Iki `top_reasons` alanina dikkat: `top_reasons` hazir okunabilir string (rapor/log icin),
`top_reasons_detail` yapisal liste (UI'da kendi formatinizi/renklendirmenizi kurmak icin
bunu kullanin).

## GET /metrics
En son egitim metriklerini doner (accuracy, precision, recall, f1, roc_auc,
confusion_matrix, classification_report). Henuz training pipeline calismadiysa 404.

---

## Bilinen sinirlamalar (frontend'i etkiler)
- `/predict/{student_id}` her cagrida `daily_data.csv`'yi baştan okuyor — buyuk
  veri/yuksek trafik olursa yavaslar, MVP'de sorun degil.
- API su an sadece localhost'ta calisiyor, deploy edilmedi. Frontend'i baska bir
  makineden/hosting'ten test edecekseniz once bir hosting karari lazim.
- Auth yok. Ayni sekilde MVP, ilk musteriye giderken eklenecek.
