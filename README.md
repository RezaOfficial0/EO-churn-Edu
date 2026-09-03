# EO Churn Early-Warning System

Predicts which mentoring students are likely to churn, and explains *why* for each
one (SHAP), so mentors can reach out before the student leaves.

- **Model:** CatBoost classifier, probability output calibrated with isotonic
  regression so `churn_probability` is a real probability.
- **Serving:** FastAPI (`api/main.py`) — single prediction, lookup by `student_id`,
  and a daily batch run.
- **Explanations:** top-N SHAP contributions per student.

> **The numbers in this repo come from a synthetic dataset.** ROC-AUC ≈ 0.74,
> PR-AUC ≈ 0.5. Treat them as a demonstration of the *system*, not as product
> performance. `model_meta.json` and `GET /metrics` both carry an
> `is_synthetic_data` flag as a reminder.

---

## Quickstart

```bash
pip install -r requirements.txt
python running_train_pipeline.py       # trains the model, writes saved_models/
uvicorn api.main:app --reload          # serves the API on http://127.0.0.1:8000
```

Interactive API docs: <http://127.0.0.1:8000/docs>.
A repo clone already ships a trained model and sample data, so step 2 is optional
for a first look.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest
```

Run in Docker:

```bash
docker build -t eo-churn .
docker run -p 8000:8000 --env-file .env eo-churn
```

---

## Project layout

```
config.py                     all tunable settings in one place (see below)
running_train_pipeline.py     `python running_train_pipeline.py` -> trains the model

data/
  mentorluk_churn_veriseti.csv  raw export (has nulls)
  updated_data.csv              training data, rebuilt by scripts/build_training_data.py
  daily_data.csv                sample "today's students" for the daily run

src/
  data/loader.py              read a CSV into a DataFrame (swap this for a DB later)
  data/features.py            THE feature-engineering recipe (nulls -> flags + impute)
  data/validation.py          reject bad data before it reaches the model
  data/preprocess.py          select FEATURES, cast categoricals, train/val/test split
  model/model.py              build the CatBoost classifier
  model/train.py              fit with early stopping on the validation set
  model/calibrate.py          isotonic calibration of the probabilities
  model/threshold.py          pick the alert threshold that minimises business cost
  model/evaluate.py           ROC-AUC, PR-AUC, precision@k, calibration, confusion matrix
  model/baseline.py           logistic-regression + one-rule baselines to beat
  model/save.py / load.py     model file + model_meta.json sidecar
  predictions/predict.py      probabilities -> at-risk table
  explainer/shap_explainer.py per-student SHAP reasons

pipeline/
  training_pipeline.py        loader -> validate -> features -> split -> train -> calibrate
                              -> threshold -> evaluate -> save (+ meta)
  daily_pipeline.py           score today's students, keep the risky ones, explain them,
                              append to data/daily_alerts.csv with new/still-at-risk status

api/main.py                   FastAPI app
scripts/
  build_training_data.py      raw CSV -> data/updated_data.csv (reproducible)
  send_daily_alerts.py        print / webhook-post today's new at-risk students
  test_api.py                 live smoke test (needs a running server)
```

---

## Configuration

Everything you would tune per deployment lives in `config.py`:

| Setting | Meaning |
|---|---|
| `TRAIN_DATA_PATH`, `DAILY_DATA_PATH`, `MODEL_PATH`, `MODEL_META_PATH` | file locations (anchored to the repo root) |
| `STUDENT_INFO` | id columns passed through to API responses, never fed to the model |
| `FEATURES` | the exact columns the model is trained and served on |
| `CAT_COLS` | which of `FEATURES` are categorical |
| `TARGET_FEATURE` | the label column (`"churn"`) |
| `MODEL_PARAMS` | CatBoost `iterations` / `depth` / `learning_rate` |
| `DECISION_COST` | relative cost of a false alarm vs. a missed churn — drives threshold selection |
| `FEATURE_BOUNDS` | accepted min/max for each numeric input (API input validation) |
| `MAX_NULL_RATIO_PER_COLUMN` | a single column above this fraction of nulls fails validation |
| `SHAP_TOP_N_FEATURES` | how many reasons to return per student |

Environment variables (optional, loaded from `.env` — see `.env.example`):
`ALLOWED_ORIGINS`, `API_KEY`, `ALERT_WEBHOOK_URL`.

---

## Daily alerts

`POST /run-daily-pipeline` scores `daily_data.csv`, keeps students at or above the
chosen threshold, and **appends** them to `data/daily_alerts.csv` with a `run_date`
and a `status` of `new` or `still_at_risk` (so the same student is not reported as
new every day).

To actually notify someone, run on a schedule, e.g. a cron entry:

```cron
# 07:00 every weekday: run the pipeline, then send the new alerts
0 7 * * 1-5  cd /path/to/EO-Churn-Edu && .venv/bin/python -m pipeline.daily_pipeline && .venv/bin/python scripts/send_daily_alerts.py
```

`send_daily_alerts.py` prints the day's `new` students, and also POSTs them to
`ALERT_WEBHOOK_URL` if that is set.

---

## Onboarding a new dataset

1. Point `TRAIN_DATA_PATH` / `DAILY_DATA_PATH` at the new files and update
   `FEATURES` / `CAT_COLS` / `STUDENT_INFO` in `config.py`.
2. Adjust the recipe in `src/data/features.py` (which columns get a missing-flag,
   which are imputed, and the group column) to match the new data.
3. `python scripts/build_training_data.py` then `python running_train_pipeline.py`.

No other file needs to change.
