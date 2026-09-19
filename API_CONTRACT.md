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

`/docs`, `/redoc` and `/openapi.json` are **not** covered by the key. They are
reachable without one even when `API_KEY` is set.

⚠️ **The dashboard cannot currently send this header** — it has no place to
configure a key. So today auth is all-or-nothing: set `API_KEY` and the dashboard
shows only errors. If you need both, put a reverse proxy in front that injects the
header server-side rather than shipping the key into the browser bundle.

## CORS

Allowed browser origins come from the `ALLOWED_ORIGINS` environment variable
(comma-separated). The default is `http://localhost:5173, http://127.0.0.1:5173`.

## Data source

The daily endpoints read students and write alerts through one switch, the
`DATA_SOURCE` environment variable: `csv` (default — `data/daily_data.csv` and
`data/daily_alerts.csv`) or `db` (the `daily_students` and `alerts` tables in
`DATABASE_URL`; schema in `db/schema.sql`, loaded by
`scripts/load_daily_students.py`). **Nothing in this contract changes between the two** — same
endpoints, same request and response shapes. If the frontend sees different data,
the server is pointed at a different backend, not at a different API.

The *shapes* match; the *failure modes* do not. A malformed row produces a
`missing required columns` error in `db` mode and a different message in `csv`
mode, and the CSV alert log cannot store `top_reasons_detail`. Neither is visible
on the endpoints below.

## Error format

Most `4xx` / `5xx` responses have this body:

```json
{"detail": "human-readable message"}
```

| Code | When |
|---|---|
| `400` | the server could not read its own data source — a missing daily-data file in `csv` mode, or a validation failure on the daily data (missing/extra column, duplicate id, a null in a column that is not imputed) |
| `401` | missing or wrong `X-API-Key` |
| `404` | `GET /predict/{student_id}` — the id is not in today's data. Also `GET /metrics` when the model has never been trained |
| `422` | a **request body or query parameter** does not match the schema |
| `500` | unexpected server error (logged with a traceback server-side; the body is always the fixed string `internal server error`) |
| `503` | the model is not loaded, or `DATA_SOURCE=db` is set without a usable `DATABASE_URL` |

Two things to code defensively around:

- **`422` does not use the shape above.** FastAPI returns a *list* of objects:
  `{"detail": [{"type", "loc", "msg", "input", "url"}, ...]}`. Rendering `detail`
  as a string gives `[object Object]` for the most common client error, so branch
  on `Array.isArray(detail)`.
- A "missing daily data file" is reported as `503` by `GET /predict/{student_id}`
  but as `400` by `GET /students` and `POST /run-daily-pipeline`. That
  inconsistency is a known defect, not a signal — do not infer anything from which
  of the two you got.

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

Score one student who is not in today's data. The body must contain **all** columns
in `config.FEATURES`, each numeric field range-checked against
`config.FEATURE_BOUNDS`; missing or out-of-range fields return `422`.

⚠️ **These are the model's *engineered* features, not a row from the client
export.** Unlike `GET /predict/{student_id}`, this endpoint does **not** run the
feature-engineering recipe for you — it validates the body and goes straight to the
model. Three of the required fields do not exist in the raw export and you must
compute them:

| Field | How to compute it |
|---|---|
| `monthly_value_try` | `monthly_fee_try / config.PLAN_MONTHS[plan_type]` — 1 for `Aylık`, 3 for `3 Aylık`, 12 for `Yıllık`. Sending the annual price unchanged is accepted (the bound allows up to 1,000,000) and silently returns a confidently wrong answer |
| `weekly_study_hours_actual_missing` | `1` if the raw value was null, else `0` |
| `satisfaction_missing` | `1` if the raw value was null, else `0` |

When a `_missing` flag is `1`, send the imputed value in the matching field — the
per-plan medians are in `model_meta.json` under `imputation_values`. Nulls are not
accepted (`satisfaction_survey_score` is bounds-checked to 1–5), and nothing
cross-checks a flag against its value, so an impossible combination validates.

If you have a raw row rather than an engineered one, prefer
`GET /predict/{student_id}` (which runs the recipe) or ask for a `/predict/raw`
endpoint — that gap is on the backlog.

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

`churn_probability` is calibrated with Platt scaling (logistic / "sigmoid"), so
0.48 is meant to read as "~48% of students that look like this churn" rather than
as a bare ranking score. Treat that as approximate: on the current synthetic data
the Brier skill over simply predicting the base rate is 0.127, i.e. the
probabilities re-rank well but are not sharp.

`impact > 0` pushes risk up, `impact < 0` pushes it down, and the list is ordered
by `abs(impact)`. **The magnitudes are not percentage points.** They are SHAP
contributions to the *uncalibrated* log-odds margin, so an impact of `+0.91` does
not mean "+91 points of probability" and the numbers must not be summed, averaged
or rendered with a `%` sign. Use them for ordering and for the direction word
("riski artırıyor" / "riski azaltıyor"); if you need a magnitude on screen, show a
relative bar rather than a number.

## GET /predict/{student_id}

Looks the student up in today's data — `data/daily_data.csv` or the
`daily_students` table, whichever `DATA_SOURCE` selects — and returns the same
prediction plus the passthrough id columns.

Unlike `POST /predict`, this endpoint **does** run the feature-engineering recipe,
so `features` below is the post-engineering input.

```json
{
  "student_id": "STU300001",
  "enrollment_date": "2025-04-10",
  "churn_probability": 0.4851,
  "features": {
    "grade": "12. Sınıf",
    "days_since_last_contact": 41.0,
    "satisfaction_survey_score": 3.6,
    "satisfaction_missing": 1,
    "...": "... every column in config.FEATURES"
  },
  "top_reasons": [ ... ]
}
```

`features` is the model's input for this student **after** feature engineering:
the `_missing` flags are computed and any null is imputed, so the values line up
with `top_reasons`. `404` if the id is not in the daily data.

⚠️ **`top_reasons` has two different types depending on the endpoint.** On
`POST /predict` and `GET /predict/{student_id}` it is the structured list shown
above. On `GET /students` and `POST /run-daily-pipeline` it is a **preformatted
string**, and the list lives under `top_reasons_detail`. One TypeScript type cannot
cover both — this is a naming defect on the backlog, not intentional.

## POST /run-daily-pipeline?threshold=0.5

Runs the daily pipeline over today's data (predict + SHAP) and returns the students
at or above `threshold`. `threshold` is optional; the default is the value chosen
during training (stored in `model_meta.json`, currently **0.29**).

`threshold` is **not range-checked**: negative values, values above 1, and `nan` /
`inf` are accepted. `nan` and `inf` end in a `500`, and a very low threshold runs
SHAP over the whole roster. Validate it on your side.

```json
{
  "churn_risk_count": 9,
  "students": [
    {
      "student_id": "STU300010",
      "enrollment_date": "2025-02-01",
      "churn_probability": 0.71,
      "status": "new",
      "features": {"grade": "12. Sınıf", "days_since_last_contact": 59.0, "...": "..."},
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
- `features` is the same post-feature-engineering input dict as in
  `GET /predict/{student_id}`, for a student-detail view.

**This endpoint writes.** Each call records a new run in the alert log
(`data/daily_alerts.csv`, or the `alerts` table when `DATA_SOURCE=db`) and that run
becomes the baseline the next run's `new` / `still_at_risk` is measured against.
Calling it to populate a dashboard view therefore corrupts `status` — use
`GET /students` for that.

## GET /students?threshold=0.5

**Read-only.** Scores today's students and returns the ones at or above
`threshold`. Writes nothing — no alert-log row, no `status` change — so the
dashboard can call it on every page load and every refresh.

`threshold` is optional; the default is the value chosen during training
(currently **0.29** — read it from `GET /metrics` rather than hardcoding it, since
it is re-chosen on every retrain). Pass `?threshold=0` to get every student scored,
sorted most-risky first.

There is no pagination and no cap: `?threshold=0` scores and SHAP-explains the
whole roster in one response.

```json
{
  "count": 9,
  "threshold": 0.29,
  "students": [
    {
      "student_id": "STU300010",
      "enrollment_date": "2025-02-01",
      "churn_probability": 0.71,
      "features": {"grade": "12. Sınıf", "days_since_last_contact": 59.0, "...": "..."},
      "top_reasons": "days_since_last_contact (+0.59), mentor_contact_freq_per_month (+0.28)",
      "top_reasons_detail": [
        {"feature": "days_since_last_contact", "impact": 0.59},
        {"feature": "mentor_contact_freq_per_month", "impact": 0.28}
      ]
    }
  ]
}
```

Each student object is the same shape as in `POST /run-daily-pipeline`, **minus
`status`**. `new` / `still_at_risk` is defined relative to the previous *recorded*
run, and this endpoint records nothing, so it has no meaningful value here. If you
need `status`, read it from the last `POST /run-daily-pipeline` response.

Use this endpoint for displaying students. Use `POST /run-daily-pipeline` only to
actually perform the day's run.

## GET /metrics

Returns the metrics of the **currently loaded** model, read from
`saved_models/model_meta.json`. The fields a frontend needs, with the values of the
model in the repo today:

```json
{
  "is_synthetic_data": true,
  "trained_at": "2026-09-17T08:25:19Z",
  "chosen_threshold": 0.29,
  "calibration_method": "sigmoid",
  "data_rows": 3384,
  "metrics": {
    "roc_auc": 0.7185,
    "average_precision": 0.5117,
    "brier_score": 0.1728,
    "precision": 0.4303,
    "recall": 0.5707,
    "f1": 0.4907,
    "precision_at_20": 0.75,
    "lift_at_20": 2.7595,
    "confusion_matrix": [[354, 139], [79, 105]]
  },
  "cv_auc_mean": 0.7406,
  "cv_auc_std": 0.0233,
  "baseline_metrics": {
    "logistic_regression": {"roc_auc": 0.7337, "average_precision": 0.5373},
    "single_rule": {"column": "days_since_last_contact", "roc_auc": 0.6868, "average_precision": 0.4864}
  }
}
```

Notes that have bitten people:

- the precision-at-k key is **`precision_at_{PRECISION_AT_K}`**, i.e.
  `precision_at_20` today — there is no field called `precision_at_k`, and no
  `precision_at_25` anywhere in the backend.
- `roc_auc` and `cv_auc_mean` are different things (0.7185 vs 0.7406). The
  held-out number is `roc_auc`; the CV figure is computed over the full dataset
  and is not an independent error bar on it.
- `chosen_threshold` moves on every retrain — it is derived from `DECISION_COST`.
  Read it, do not hardcode it. If `/metrics` fails the dashboard has no threshold
  and should say so rather than guess one.
- **the response currently contains more than this.** `api/main.py` returns the
  whole meta file, which also includes absolute developer paths (`data_file`,
  `calibrator_path`), `data_sha256`, `imputation_values`, `model_params` and a
  per-demographic false-negative breakdown. Do not build on those fields — they
  are about to be removed behind an allow-list.

`404` if the model has never been trained.

---

## Known limitations (relevant to the frontend)

- `GET /predict/{student_id}` and `GET /students` re-read and re-score the whole
  daily dataset on every call, with no pagination and no cap — fine for the MVP,
  slow at large scale.
- **No endpoint exposes alert history.** `status`, past probabilities and the
  per-run log are recorded in the backend but not readable over HTTP, which is why
  the dashboard's 14-day trend, the "consecutive days on the list" counter and the
  probability delta column have no data source and render "yok". They need a
  `GET /students/{id}/history` (or similar) first.
- `GET /students` deliberately returns **no** `status` field, because it records no
  run. Do not fall back to `POST /run-daily-pipeline` to get one — that writes.
- The model is loaded once at startup and never refreshed, so after a retrain the
  API serves the old model until it is restarted. There is no reload endpoint.
- `GET /health` reports `ok` as soon as the model object exists. The explainer,
  the calibrator and `model_meta.json` load *after* it, so a partly-initialised
  instance can report healthy and then fail every scoring call. Treat a successful
  `/metrics` as the real readiness signal.
- The API is not deployed anywhere yet, and nothing in the repo terminates TLS,
  rate-limits, or caps request size. Testing from another machine needs a hosting
  decision first.
