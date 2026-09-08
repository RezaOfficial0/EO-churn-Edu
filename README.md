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

Check that the whole backend actually works, end to end:

```bash
python scripts/verify_backend.py
```

It walks the real chain — model → data source → scoring → alert log → API — and
prints a pass/fail line for each step. Read-only by default; exit code 0 means
everything passed, so it doubles as a smoke test. See
[Verifying the backend](#verifying-the-backend).

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

db/
  schema.sql                  Postgres schema for the daily pipeline (DATA_SOURCE=db)
  migrations/                 changes to apply to a database created before a schema change

src/
  data/loader.py              CSV and Postgres backends for the daily data + alert log,
                              switched by DATA_SOURCE (training stays CSV-only)
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
  init_db.py                  apply db/schema.sql + db/migrations/ (no psql needed)
  load_daily_students.py      CSV -> the daily_students table (how data gets into the DB)
  verify_backend.py           run the whole chain end to end and report what works
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
`DATA_SOURCE`, `DATABASE_URL`, `ALLOWED_ORIGINS`, `API_KEY`, `ALERT_WEBHOOK_URL`.

---

## Database mode

By default the daily pipeline reads `data/daily_data.csv` and appends to
`data/daily_alerts.csv`. Setting `DATA_SOURCE=db` swaps both for Postgres tables —
same endpoints, same behaviour, same response shapes. Training is unaffected either
way: it always reads CSV.

The switch exists so the two backends can run side by side. If the database is
down, `DATA_SOURCE=csv` still works; if a CSV gets corrupted, `db` still works.

```bash
createdb eo_churn                     # or create it in your Postgres client (DataGrip, pgAdmin)

# put DATABASE_URL in .env, then:
python scripts/init_db.py             # apply db/schema.sql + db/migrations/
python scripts/load_daily_students.py # load today's students (or: ... path/to/students.csv)

# flip the switch in .env
DATA_SOURCE=db
```

`init_db.py` connects through the same driver the application uses, so it needs no
`psql` on your PATH — if it works, the pipeline's connection works. It applies the
schema, then every migration, then verifies the result; all three steps are safe to
re-run. `python scripts/init_db.py --verify` checks without changing anything.

Creating the *database* itself is the one step it will not do for you: Postgres has
no `CREATE DATABASE IF NOT EXISTS`, and creating one silently would turn a typo in
`DATABASE_URL` into a mysteriously empty database instead of an error.

Re-run `load_daily_students.py` whenever the day's data changes — students are
matched on `student_id` and updated in place, so running it twice is harmless. The
CSV is validated before anything is written, so a file with a missing column or a
duplicate id is rejected rather than half-loaded.

`db/schema.sql` is safe to re-run — every statement is `IF NOT EXISTS`. That also
means it will not *alter* an existing table, which is what `db/migrations/` is for:
files that bring a database created before a schema change in line. `init_db.py`
applies both, in order, so you do not have to track which is which.

Two tables (`db/schema.sql`):

| Table | What it holds |
|---|---|
| `daily_students` | today's students — id columns natively, every feature in a `features` JSONB blob |
| `alerts` | one row per at-risk student per run, append-only (this is the `daily_alerts.csv` equivalent) |

`features` is JSONB rather than 24 typed columns because the feature list is per
client: re-pointing the system at a new dataset means editing `config.FEATURES`, and
typed columns would make that a migration every time. `loader.py` coerces the values
back to numeric on read, so nothing downstream can tell the difference.

The database tests are skipped unless you give them a throwaway database:

```bash
createdb eo_churn_test
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/eo_churn_test pytest
```

The tests apply `db/schema.sql` themselves, so the database only has to exist.

Everything else in the suite is pinned to the CSV backend by an autouse fixture in
`tests/conftest.py`, regardless of what `DATA_SOURCE` says in your `.env` — a test
run must not depend on the environment, and must never write to the database you
actually use.

---

## Verifying the backend

`scripts/verify_backend.py` is the "does this thing work?" command. It runs the real
code paths the API uses and reports each step:

```
[1/7] Environment          DATA_SOURCE, DATABASE_URL, auth
[2/7] Model                loads the model, calibrator and threshold
[3/7] Data source          reads today's students, validates them
[4/7] CSV/DB parity        (db mode) the DB returns exactly what the CSV does
[5/7] Scoring              scores, and proves nothing was written
[6/7] Daily run            (--write) records a run, checks the status values
[7/7] API                  /health, /students, /metrics, /predict/{id}
```

```bash
python scripts/verify_backend.py               # current DATA_SOURCE, writes nothing
python scripts/verify_backend.py --source db   # force the database backend
python scripts/verify_backend.py --write       # also record a real daily run
python scripts/verify_backend.py --train       # retrain first, then check everything
```

Exit code is 0 only if every step passed. Without `--write` nothing is inserted into
`alerts` or appended to `daily_alerts.csv`, so it is safe against a live setup — step
5 actively asserts that scoring had no side effects.

### The whole chain from scratch

```bash
pip install -r requirements.txt
python running_train_pipeline.py                   # 1. train -> saved_models/

createdb eo_churn                                  # 2. create the database
# put DATABASE_URL in .env, then:
python scripts/init_db.py                          #    apply the schema
python scripts/load_daily_students.py              # 3. CSV -> daily_students

# 4. flip DATA_SOURCE=db in .env, then run the chain and look at it:
python scripts/verify_backend.py --write

python -m pipeline.daily_pipeline                   # 5. or just run the daily pipeline
python scripts/send_daily_alerts.py                 #    and print the new alerts
uvicorn api.main:app --reload                       # 6. or serve it
```

Steps 1–3 are one-time. After that, the day-to-day loop is: refresh
`daily_students` (step 3), run the pipeline (step 5).

---

## Daily alerts

`POST /run-daily-pipeline` scores `daily_data.csv`, keeps students at or above the
chosen threshold, and **appends** them to `data/daily_alerts.csv` with a `run_at`
timestamp and a `status` of `new` or `still_at_risk` (so the same student is not
reported as new every run).

To actually notify someone, run on a schedule, e.g. a cron entry:

```cron
# 07:00 every weekday: run the pipeline, then send the new alerts
0 7 * * 1-5  cd /path/to/EO-Churn-Edu && .venv/bin/python -m pipeline.daily_pipeline && .venv/bin/python scripts/send_daily_alerts.py
```

`send_daily_alerts.py` prints the day's `new` students, and also POSTs them to
`ALERT_WEBHOOK_URL` if that is set. Under `DATA_SOURCE=db` both of them read and
write the `alerts` table instead of the CSV; nothing else about the flow changes.

Note that `POST /run-daily-pipeline` is the *writing* endpoint. A dashboard that
just wants to display students should call `GET /students`, which scores without
recording a run — see `API_CONTRACT.md`.

---

## Onboarding a new dataset

1. Point `TRAIN_DATA_PATH` / `DAILY_DATA_PATH` at the new files and update
   `FEATURES` / `CAT_COLS` / `STUDENT_INFO` in `config.py`.
2. Adjust the recipe in `src/data/features.py` (which columns get a missing-flag,
   which are imputed, and the group column) to match the new data.
3. `python scripts/build_training_data.py` then `python running_train_pipeline.py`.

No other file needs to change.
