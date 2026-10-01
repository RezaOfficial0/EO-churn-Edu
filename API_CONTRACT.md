# API Contract — Churn Early-Warning System

For the frontend. You do not need to read the code; while the server is running the
interactive Swagger UI at `http://127.0.0.1:8000/docs` lets you try every endpoint —
it is behind the API key like everything else, so a browser needs something that
adds the `X-API-Key` header (the dashboard has one; see
[Reaching the API from a browser](#reaching-the-api-from-a-browser)).

## Running it

```bash
pip install -r requirements.txt
python running_train_pipeline.py      # only if saved_models/ is empty
uvicorn api.main:app --reload
```

Base URL: `http://127.0.0.1:8000`

## Authentication

Every request except `GET /health` must send the server's `API_KEY` value in an
`X-API-Key` header. A missing or wrong key returns `401`. This includes the
generated documentation: `/docs`, `/redoc` and `/openapi.json` are behind the key
too, so opening Swagger UI in a browser needs a proxy (or a browser extension) that
adds the header.

The server **refuses to start** when `API_KEY` is unset. The single exception is
local development: `EOAI_ALLOW_NO_AUTH=1` together with a loopback `API_BIND_HOST`
(e.g. `127.0.0.1`) starts an unauthenticated server and logs a warning. Any other
combination is a startup error, not a warning.

## Reaching the API from a browser

A browser must not hold the key. Anything a page can read, anyone who opens
devtools can read, so a key shipped in a JavaScript bundle is not authentication —
it is a published secret.

The supported pattern is a **reverse proxy in front of the page**, adding the
header server-side. The dashboard does exactly this: it is served by nginx, which
proxies its own `/api/` prefix to this API and injects `X-API-Key` from a runtime
environment variable.

```
browser ──/api/students──▶ dashboard nginx ──/students + X-API-Key──▶ this API
```

What that means for a frontend calling this contract:

- **the base URL is a path, not an address.** The dashboard's `VITE_API_BASE`
  defaults to `/api`, so every request goes to the page's own origin. Strip the
  prefix in the proxy (`proxy_pass http://api:8000/;` — the trailing slash does
  it), and the paths in this document are what the API receives.
- **the API port does not have to be published.** The proxy runs next to the API,
  so it can use the internal address (`http://api:8000` in compose). Publishing
  the port is then only for `curl` and `/docs`.
- **`/health` still needs no key**, but sending one is harmless, so a proxy can
  inject the header unconditionally.
- **an empty key means no header.** nginx omits a header whose value is an empty
  string, which is the local demo mode where this API runs with
  `EOAI_ALLOW_NO_AUTH=1`. The same proxy configuration works in both modes.

## CORS

Allowed browser origins come from the `ALLOWED_ORIGINS` environment variable
(comma-separated). The default is `http://localhost:5173, http://127.0.0.1:5173`.

A same-origin frontend behind the proxy above sends **no cross-origin request at
all**, so this list does not apply to it — `ALLOWED_ORIGINS` only matters for a
browser client served from a different origin than the API.

## Data source

The daily endpoints read students and write alerts through one switch, the
`DATA_SOURCE` environment variable: `csv` (default — `data/daily_data.csv` and
`data/daily_alerts.csv`) or `db` (the `daily_students` and `alerts` tables in
`DATABASE_URL`; schema in `db/schema.sql`, loaded by
`scripts/load_daily_students.py`). **Nothing in this contract changes between the two** — same
endpoints, same request and response shapes. If the frontend sees different data,
the server is pointed at a different backend, not at a different API.

## Error format

Every `4xx` / `5xx` response has this body:

```json
{"detail": "human-readable message"}
```

- `400` — the data you sent is invalid (bad column, out-of-range value, unknown student).
- `401` — missing or wrong `X-API-Key`.
- `404` — no such student, or no such route.
- `422` — request body or query parameter does not match the schema (missing field,
  wrong type, unknown category, extra field, value outside the allowed range). The
  `detail` string names the fields and the constraints they broke; it never echoes
  the value you sent.
- `500` — unexpected server error (also logged with a traceback on the server).
- `503` — the API is up but not ready to score: `detail` names the components that
  did not load (`model`, `explainer`, `calibrator`, `meta`).

---

## GET /health

```json
{
  "status": "ok",
  "components": {"model": true, "explainer": true, "calibrator": true, "meta": true}
}
```

`status` is `ok` only when **all four** components are loaded. If any is missing the
status is `degraded`, that component is `false`, and every scoring endpoint returns
`503` — including when only the calibrator is missing, because without it the
probabilities would be raw CatBoost scores while the alert threshold was chosen on
calibrated ones.

No authentication, and no other detail: this is the one endpoint anyone who can reach
the port may call, so it never returns a file path or a load error. The reason is in
the server's startup log. A container healthcheck must test `status == "ok"`, not
just the 200.

## POST /predict

Send one student's raw feature values, get a churn prediction. The body must contain
**exactly** the columns in `config.FEATURES` — no more, no less. Anything else is a
`422`:

| Field group | Accepted |
|---|---|
| categorical (`grade`, `track`, `city_tier`, `parent_involvement`, `plan_type`) | only the levels in `config.CATEGORICAL_LEVELS`. An unlisted value like `"banana"` is rejected rather than scored — CatBoost would hash it and return a plausible-looking probability |
| counters and day counts (`config.INTEGER_FEATURES`) | whole numbers only: `3`, not `2.7`, and not `true` |
| `_missing` flags (`config.FLAG_FEATURES`) | `0` or `1` as integers |
| every other numeric | a finite number inside `config.FEATURE_BOUNDS`. `NaN`, `Infinity` and booleans are rejected |
| anything not in `config.FEATURES` | rejected — an extra column would otherwise become model feature #25. That now includes `mentor_contact_freq_per_month`, `days_since_last_contact` and `days_to_next_exam`, which the B-21 leakage audit removed (`docs/LEAKAGE_AUDIT.md`): `POST /predict` **422**s on them. The daily batch path is deliberately laxer — a client's CSV export may keep sending them and they are dropped by name before the validation gate |

Response:

```json
{
  "churn_probability": 0.4851,
  "top_reasons": [
    {"feature": "program_adherence_rate", "impact": -0.27},
    {"feature": "message_response_time_hours", "impact": 0.18},
    {"feature": "payment_delay_days_avg", "impact": 0.07}
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
  "features": {
    "grade": "12. Sınıf",
    "program_adherence_rate": 0.41,
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

The passthrough id columns (`student_id`, `enrollment_date`) are **not** imputed —
they are never model inputs — so they can be `null` when the daily data has no
value. A null there is missing data and arrives as JSON `null`; it used to produce a
`500` on this endpoint and on `GET /students`.

A `400` with `"row cannot be scored: N required value(s) missing"` means this one
student's row has a hole nothing can fill — the same rule `GET /students`
quarantines a row by (B-28). The count is all the body carries; which columns is in
the server log, because the body is rendered by clients and logged by proxies.
Unlike a batch, a single named student has no "score the rest" to fall back on.

## The day's run (no longer an endpoint)

`POST /run-daily-pipeline` **has been removed.** It was the only write on the HTTP
surface, and every call recorded a new run in the alert log — which is the baseline
the next run's `new` / `still_at_risk` is measured against. A double-clicked button
was therefore enough to mark every student `still_at_risk` and send the morning
message out with an empty "new students" section. There was no CSRF token, no lock
and no idempotency guard behind it.

The day's run now belongs to the scheduler:

```bash
python -m pipeline.daily_pipeline
```

For the frontend this means: there is nothing to trigger. Use `GET /students` for
everything you display. `status` (`new` / `still_at_risk`) is not available over
HTTP; it is written to the alert log by the scheduled run and read from there by
`scripts/send_daily_alerts.py`.

## GET /students?threshold=0.5

**Read-only.** Scores today's students and returns the ones at or above
`threshold`. Writes nothing — no alert-log row, no `status` change — so the
dashboard can call it on every page load and every refresh.

`threshold` is optional and must satisfy `0 <= threshold <= 1`; anything else
(including `nan` and `-inf`) is a `422`. The default is the value chosen during
training. Pass `?threshold=0` to get every student scored, sorted most-risky first.

```json
{
  "count": 9,
  "skipped_count": 0,
  "threshold": 0.53,
  "students": [
    {
      "student_id": "STU300010",
      "enrollment_date": "2025-02-01",
      "churn_probability": 0.71,
      "features": {"grade": "12. Sınıf", "program_adherence_rate": 0.39, "...": "..."},
      "top_reasons": "program_adherence_rate (+0.59), message_response_time_hours (+0.28)",
      "top_reasons_detail": [
        {"feature": "program_adherence_rate", "impact": 0.59},
        {"feature": "message_response_time_hours", "impact": 0.28}
      ]
    }
  ]
}
```

There is no `status` field. `new` / `still_at_risk` is defined relative to the
previous *recorded* run, and this endpoint records nothing, so it would have no
meaningful value here.

`skipped_count` (B-28) is how many rows of today's data could **not** be scored: a
row missing a value nothing can impute (a null `grade`, `plan_type`,
`program_adherence_rate`, …, or a null `student_id`) is quarantined instead of
failing the whole request. A null `weekly_study_hours_actual` or
`satisfaction_survey_score` is *not* one of them — the imputer fills those and the
`*_missing` flags record that it did, so those rows are scored normally.

`count` therefore counts the students **at or above the threshold**, and
`count + (scored but below the threshold) + skipped_count` is the whole file. A
dashboard should show `skipped_count` when it is not 0: the list is quietly shorter
otherwise, and nothing else on the HTTP surface says why. The rejected rows
themselves are not returned (they are student records) and no student id appears in
any log line about them. Above `MAX_QUARANTINE_RATIO` (default 10% of the input),
or when no row at all is usable, the endpoint returns `400` instead — a shorter list
is acceptable, a list built from half a broken export is not.

This is the endpoint for displaying students. It is also the only one that scores a
whole cohort, now that the write endpoint is gone.

## GET /schema

The feature contract, as data. Published so a consumer can **verify** the feature
set instead of keeping a hand-written copy of it.

Two services read our scored output and hold their own table keyed by feature name:
the dashboard (labels) and the retention app (one policy rule per feature). A copy
drifts silently. It already did: B-21 removed three columns from `FEATURES`, no
endpoint changed shape, nothing errored, and the retention app's rule file simply
stopped covering the features the model now reports — half the students fell back
to a generic action. A silent downgrade is worse than a 500.

`feature_set_hash` is the fix. Pin it in your own test; when it changes, your test
fails and you look at what moved, instead of shipping a half-blind consumer.

```json
{
  "schema_version": 1,
  "feature_set_hash": "4df4cfbe44acf864...",
  "id_field": "student_id",
  "info_fields": ["student_id", "enrollment_date"],
  "top_reasons_count": 3,
  "features": [
    { "name": "grade", "label": "Sınıf", "type": "categorical", "is_flag": false,
      "levels": ["11. Sınıf", "12. Sınıf", "Mezun"] },
    { "name": "tenure_months", "label": "Programdaki süresi (ay)", "type": "number",
      "is_flag": false, "min": 0, "max": 600, "integer": false },
    { "name": "satisfaction_missing", "label": "Memnuniyet anketi doldurulmamış",
      "type": "flag", "is_flag": true, "min": 0, "max": 1, "integer": true }
  ]
}
```

- `type` is `categorical` (has `levels`), `number` or `flag` (also `number`-shaped,
  always 0 or 1).
- `is_flag: true` means the value next to it was **imputed, not measured**. Showing
  the number without reading the flag claims a measurement nobody made.
- `info_fields` are returned next to the features on `/students` and `/predict` and
  are never model input — a consumer that allow-lists feature names needs to know
  they exist so it does not reject the record for carrying them.
- `features` is in the model's own order.
- Answers **without a trained model**: it reads configuration, not model state, so a
  consumer can check the contract at startup.
- Behind the API key like every other data route. Not secret, but it is the exact
  shape of a valid `/predict` body and an unauthenticated service should not hand
  that out. Training statistics, file paths and the error breakdown are not here.

---

## GET /metrics

Returns a fixed, allow-listed subset of `saved_models/model_meta.json` — the
metrics of the **currently loaded** model, and nothing else. `model_meta.json`
itself also holds filesystem paths, the training data hash, the imputation medians,
the hyperparameters and a per-segment false-negative breakdown; none of those are
published here, and a new key added to the file is not published either until it is
added to the allow-list.

The response contains exactly these fields (any that the training run did not
produce are simply absent):

```json
{
  "is_synthetic_data": true,
  "trained_at": "2026-09-28T08:10:24Z",
  "chosen_threshold": 0.29,
  "calibration_method": "sigmoid",
  "data_rows": 3384,
  "metrics": { "roc_auc": 0.600, "average_precision": 0.369, "precision_at_20": 0.35, "precision_at_k_requested": 20, "precision_at_k_effective": 20, "...": "..." },
  "cv_auc_mean": 0.640,
  "cv_auc_std": 0.036,
  "baseline_metrics": { "logistic_regression": { "...": "..." }, "single_rule": { "...": "..." } }
}
```

`404` if the model has never been trained.

---

## Known limitations (relevant to the frontend)

- `GET /predict/{student_id}` and `GET /students` re-read the whole daily dataset on
  every call — fine for the MVP, slow at large scale.
- The API is not deployed anywhere yet. Testing from another machine needs a hosting
  decision first.
