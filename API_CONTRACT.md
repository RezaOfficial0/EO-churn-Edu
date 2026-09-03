# API Contract — Churn Early-Warning System

For the frontend. You do not need to read the code; while the server is running the
interactive Swagger UI at `http://127.0.0.1:8000/docs` lets you try every endpoint.

## Running it

```bash
pip install -r requirements.txt
python running_train_pipeline.py      # only if saved_models/ is empty
uvicorn api.main:app --reload
```

Base URL: `http://127.0.0.1:8000`

## Authentication

If the server is started with the `API_KEY` environment variable set, every request
except `GET /health` must send that value in an `X-API-Key` header. A missing or
wrong key returns `401`. If `API_KEY` is not set, authentication is disabled (local
development only) and the server logs a warning at startup.

## CORS

Allowed browser origins come from the `ALLOWED_ORIGINS` environment variable
(comma-separated). The default is `http://localhost:5173, http://127.0.0.1:5173`.

## Error format

Every `4xx` / `5xx` response has this body:

```json
{"detail": "human-readable message"}
```

- `400` — the data you sent is invalid (bad column, out-of-range value, unknown student).
- `401` — missing or wrong `X-API-Key`.
- `422` — request body does not match the schema (missing field, wrong type, value
  outside the allowed range).
- `500` — unexpected server error (also logged with a traceback on the server).
- `503` — the model is not loaded; the API is up but cannot score yet.

---

## GET /health

```json
{"status": "ok"}
```

or, if the model failed to load:

```json
{"status": "degraded", "reason": "model file not found: ..."}
```

## POST /predict

Send one student's raw feature values, get a churn prediction. The body must contain
**all** columns in `config.FEATURES`. Each numeric field is range-checked against
`config.FEATURE_BOUNDS`; out-of-range or missing fields return `422`.

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

`churn_probability` is calibrated (isotonic), so 0.48 really means "~48% of students
that look like this churn". `impact > 0` pushes risk up, `impact < 0` pushes it down.

## GET /predict/{student_id}

Looks the student up in the current `daily_data.csv` and returns the same prediction
plus the passthrough id columns.

```json
{
  "student_id": "STU300001",
  "enrollment_date": "2025-04-10",
  "churn_probability": 0.4851,
  "top_reasons": [ ... ]
}
```

`404` if the id is not in the daily data.

## POST /run-daily-pipeline?threshold=0.5

Runs the daily pipeline over `daily_data.csv` (predict + SHAP) and returns the
students at or above `threshold`. `threshold` is optional; the default is the value
chosen during training (stored in `model_meta.json`).

```json
{
  "churn_risk_count": 9,
  "students": [
    {
      "student_id": "STU300010",
      "enrollment_date": "2025-02-01",
      "churn_probability": 0.71,
      "status": "new",
      "top_reasons": "days_since_last_contact (+0.59), mentor_contact_freq_per_month (+0.28)",
      "top_reasons_detail": [
        {"feature": "days_since_last_contact", "impact": 0.59},
        {"feature": "mentor_contact_freq_per_month", "impact": 0.28}
      ]
    }
  ]
}
```

- `status` is `new` (first time at risk) or `still_at_risk` (also at risk in the
  previous run).
- `top_reasons` is a ready-to-display string; `top_reasons_detail` is the structured
  list — use whichever suits your UI.

Each run also appends its results to `data/daily_alerts.csv`.

## GET /metrics

Returns the metrics of the **currently loaded** model, read from
`saved_models/model_meta.json`:

```json
{
  "is_synthetic_data": true,
  "trained_at": "2026-09-03T12:00:00Z",
  "chosen_threshold": 0.53,
  "metrics": { "roc_auc": 0.74, "average_precision": 0.5, "precision_at_k": ..., "...": "..." },
  "cv_auc_mean": 0.74,
  "cv_auc_std": 0.02,
  "baseline_metrics": { "...": "..." }
}
```

`404` if the model has never been trained.

---

## Known limitations (relevant to the frontend)

- `GET /predict/{student_id}` re-reads `daily_data.csv` on every call — fine for the
  MVP, slow at large scale.
- The API is not deployed anywhere yet. Testing from another machine needs a hosting
  decision first.
