# EO Churn Early-Warning System

Predicts which mentoring students are likely to leave the programme, explains
**why** for each one, and delivers that to a mentor every morning — so someone can
reach out before the student is gone.

| | |
|---|---|
| **Model** | CatBoost classifier, calibrated with isotonic regression so `churn_probability` is a real probability, not a ranking score |
| **Explanations** | top-N SHAP contributions per student, in plain Turkish |
| **Serving** | FastAPI — single prediction, lookup by `student_id`, read-only listing, daily batch run |
| **Storage** | CSV files or PostgreSQL, switched by one environment variable |
| **Delivery** | Telegram, email (SMTP), or webhook |

> **The numbers in this repo come from a synthetic dataset.** ROC-AUC ≈ 0.71,
> PR-AUC ≈ 0.49. They demonstrate that the *system* works; they say nothing about
> how well churn can actually be predicted for a real client. `model_meta.json`
> and `GET /metrics` both carry an `is_synthetic_data` flag as a reminder.

---

## Quickstart

A fresh clone already ships a trained model and sample data, so this is enough to
see it working:

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/verify_backend.py     # proves the whole chain works, writes nothing
uvicorn api.main:app --reload        # http://127.0.0.1:8000/docs
```

That runs on CSV files. To use PostgreSQL instead, see
[Data sources](#data-sources-csv-or-postgres).

---

## Command reference

Every command in the project, in the order you would meet them.

### Setup

| Command | What it does |
|---|---|
| `pip install -r requirements.txt` | runtime dependencies |
| `pip install -r requirements-dev.txt` | test dependencies (pytest, httpx) |
| `cp .env.example .env` | create your local settings file — every value is optional |

### Training

| Command | What it does |
|---|---|
| `python scripts/build_training_data.py` | raw CSV → `data/updated_data.csv` (reproducible feature engineering) |
| `python running_train_pipeline.py` | train, calibrate, pick the threshold, evaluate, write `saved_models/` |

Training always reads CSV and ignores `DATA_SOURCE`.

### Database (only for `DATA_SOURCE=db`)

| Command | What it does |
|---|---|
| `createdb eo_churn` | create the database (or do it in DataGrip / pgAdmin) |
| `python scripts/init_db.py` | apply `db/schema.sql` + every file in `db/migrations/`, then verify |
| `python scripts/init_db.py --verify` | check the schema, change nothing |
| `python scripts/init_db.py --create-db` | also create the database named in `DATABASE_URL` if missing |
| `python scripts/load_daily_students.py` | load `data/daily_data.csv` into `daily_students` |
| `python scripts/load_daily_students.py path/to/other.csv` | load a different file |

### The daily run

| Command | What it does |
|---|---|
| `python -m pipeline.daily_pipeline` | score today's students, record the run in the alert log |
| `python scripts/send_daily_alerts.py` | deliver the latest run's alert to the configured channels |
| `python scripts/send_daily_alerts.py --dry-run` | print the message, send nothing |
| `python scripts/send_daily_alerts.py --channels telegram` | override `NOTIFY_CHANNELS` for one run |
| `python scripts/send_daily_alerts.py --force` | send even with no run, or a stale one |
| `python scripts/send_daily_alerts.py --max-age-hours 12` | tighten the staleness limit (default 24) |

### Notification setup

| Command | What it does |
|---|---|
| `python scripts/telegram_setup.py` | list the chats the bot can see, print the id for `.env` |
| `python scripts/telegram_setup.py --test` | send a test message to `TELEGRAM_CHAT_ID` |

### Serving

| Command | What it does |
|---|---|
| `uvicorn api.main:app --reload` | run the API on `http://127.0.0.1:8000` |
| `python scripts/test_api.py` | live smoke test against a running server, writes nothing |
| `python scripts/test_api.py --write` | also test the writing endpoint (records a daily run) |

### Verifying and testing

| Command | What it does |
|---|---|
| `python scripts/verify_backend.py` | run the whole chain, report each step, write nothing |
| `python scripts/verify_backend.py --source db` | force the database backend for this check |
| `python scripts/verify_backend.py --write` | also record a real daily run |
| `python scripts/verify_backend.py --train` | retrain first, then check everything |
| `pytest` | the test suite (database tests skip without `TEST_DATABASE_URL`) |
| `TEST_DATABASE_URL=postgresql://postgres@localhost:5432/eo_churn_test pytest` | including the database tests |

### Docker

| Command | What it does |
|---|---|
| `docker build -t eo-churn .` | build the API image |
| `docker run -p 8000:8000 --env-file .env eo-churn` | run it |

---

## How it works

Three flows. They share the feature-engineering recipe and nothing else, which is
what keeps training reproducible and serving fast.

**1. Training** — occasional, manual, always CSV:

```
data/updated_data.csv
  -> validate            reject bad data before it reaches the model
  -> feature engineering missing-flags, then group-median imputation
  -> train/val/test split
  -> CatBoost + early stopping
  -> isotonic calibration        (on the validation set)
  -> threshold selection         (minimises DECISION_COST)
  -> evaluate + save             saved_models/ + model_meta.json
```

`model_meta.json` travels with the model and carries the imputation medians and
the chosen threshold, so serving reproduces training exactly.

**2. The daily run** — every morning:

```
daily_data.csv  OR  daily_students table     (DATA_SOURCE decides)
  -> validate
  -> feature engineering        same recipe, medians from model_meta.json
  -> score + calibrate
  -> keep students at or above the threshold
  -> SHAP for those students only        (all of them would be the bottleneck)
  -> mark new / still_at_risk vs. the previous run
  -> append to daily_alerts.csv  OR  the alerts table
```

Split in two on purpose: `score_students()` is pure, `log_alerts()` is the only
writer. That is what lets a dashboard score on every page load without corrupting
the `new` / `still_at_risk` history.

**3. Notification** — after the run:

```
alert log -> the run's new students + a summary of repeats
          -> Turkish message (FEATURE_LABELS)
          -> Telegram / email / webhook
```

---

## Project layout

```
config.py                     every tunable setting (see Configuration)
running_train_pipeline.py     entry point: train the model

data/
  mentorluk_churn_veriseti.csv  raw export (has nulls)
  updated_data.csv              training data, rebuilt by scripts/build_training_data.py
  daily_data.csv                sample "today's students" for the daily run
  daily_alerts.csv              the alert log in CSV mode (gitignored, regenerated)

db/
  schema.sql                  Postgres schema for the daily pipeline
  migrations/                 changes for a database created before a schema change

src/
  data/loader.py              CSV and Postgres backends for daily data + the alert log,
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
  notifications/
    message.py                alert rows -> Turkish message (plain text + HTML)
    channels.py               Telegram, SMTP email, webhook
    notify.py                 pick the configured channels, survive one failing

pipeline/
  training_pipeline.py        the training flow above
  daily_pipeline.py           score_students() (pure) + log_alerts() (writes)

api/main.py                   FastAPI app

scripts/
  build_training_data.py      raw CSV -> data/updated_data.csv
  init_db.py                  apply db/schema.sql + db/migrations/ (no psql needed)
  load_daily_students.py      CSV -> the daily_students table
  send_daily_alerts.py        deliver today's alert to Telegram / email / webhook
  telegram_setup.py           find the chat id for .env, prove the bot can reach it
  verify_backend.py           run the whole chain end to end and report what works
  test_api.py                 live smoke test (needs a running server; read-only by default)

tests/                        pytest suite (77 tests)
```

---

## Configuration

Everything you would tune per deployment lives in `config.py`:

| Setting | Meaning |
|---|---|
| `TRAIN_DATA_PATH`, `DAILY_DATA_PATH`, `MODEL_PATH`, `MODEL_META_PATH` | file locations, anchored to the repo root |
| `STUDENT_INFO` | id columns passed through to API responses, never fed to the model |
| `FEATURES` | the exact columns the model is trained and served on |
| `CAT_COLS` | which of `FEATURES` are categorical |
| `TARGET_FEATURE` | the label column (`"churn"`) |
| `MODEL_PARAMS` | CatBoost `iterations` / `depth` / `learning_rate` |
| `DECISION_COST` | relative cost of a false alarm vs. a missed churn — drives threshold selection |
| `PRECISION_AT_K` | how many students a mentor can realistically contact per run |
| `FEATURE_BOUNDS` | accepted min/max per numeric input (API input validation) |
| `MAX_NULL_RATIO_PER_COLUMN` | a single column above this fraction of nulls fails validation |
| `SHAP_TOP_N_FEATURES` | how many reasons to return per student |
| `FEATURE_LABELS` | Turkish label per feature, used in the daily alert message |

Everything environment-specific goes in `.env` (see `.env.example` — every variable
is optional and documented there):

| Variable | Purpose |
|---|---|
| `DATA_SOURCE` | `csv` (default) or `db` |
| `DATABASE_URL` | Postgres connection string, required for `db` |
| `API_KEY` | shared secret for the `X-API-Key` header; unset disables auth |
| `ALLOWED_ORIGINS` | CORS origins allowed to call the API |
| `NOTIFY_CHANNELS` | `telegram`, `email`, `webhook` — comma-separated, empty = print only |
| `NOTIFY_TITLE` | heading at the top of every alert message |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram delivery |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`, `SMTP_STARTTLS` | email delivery |
| `ALERT_WEBHOOK_URL` | Slack / Discord incoming webhook |

`.env` is gitignored. Never commit it — it holds the database password, the API key
and the bot token.

---

## Data sources: CSV or Postgres

By default the daily pipeline reads `data/daily_data.csv` and appends to
`data/daily_alerts.csv`. Setting `DATA_SOURCE=db` swaps both for Postgres tables.
**Nothing else changes** — same endpoints, same behaviour, same response shapes.
Training is unaffected either way: it always reads CSV.

The switch exists so the two can run side by side. If the database is down,
`DATA_SOURCE=csv` still works; if a CSV gets corrupted, `db` still works. It is a
fallback, not a migration you have to finish.

### Setting up Postgres

```bash
createdb eo_churn                      # or create it in DataGrip / pgAdmin

# put DATABASE_URL in .env, then:
python scripts/init_db.py              # schema + migrations, then verify
python scripts/load_daily_students.py  # CSV -> daily_students

# finally, in .env:
DATA_SOURCE=db
```

`init_db.py` connects through the same driver the application uses, so it needs no
`psql` on your PATH — if it works, the pipeline's connection works. It applies the
schema, then every migration, then verifies the result; all three are safe to
re-run. `--verify` checks without changing anything.

Creating the *database itself* is opt-in (`--create-db`) rather than automatic: a
typo in `DATABASE_URL` would otherwise produce a mysteriously empty database
instead of an error. Without the flag, a missing database is reported along with
the databases that do exist on that server — usually enough to spot the typo.

Re-run `load_daily_students.py` whenever the day's data changes. Students are
matched on `student_id` and updated in place, so running it twice is harmless. The
CSV is validated first, so a file with a missing column, a stray extra column or a
duplicate id is rejected rather than half-loaded.

### The schema

| Table | What it holds |
|---|---|
| `daily_students` | today's students — id columns natively, every feature in a `features` JSONB blob |
| `alerts` | one row per at-risk student per run, append-only (the `daily_alerts.csv` equivalent) |

`features` is JSONB rather than 24 typed columns because the feature list is per
client: re-pointing the system at a new dataset means editing `config.FEATURES`,
and typed columns would make that a migration every time. `loader.py` coerces the
values back to numeric on read, so nothing downstream can tell the difference.

`db/schema.sql` is safe to re-run — every statement is `IF NOT EXISTS`. That also
means it will not *alter* an existing table, which is what `db/migrations/` is for:
files that bring an older database in line. `init_db.py` applies both in order, so
you never have to track which is which.

---

## The daily run

```bash
python -m pipeline.daily_pipeline     # score and record
python scripts/send_daily_alerts.py   # notify
```

The first command scores today's students, keeps the ones at or above the chosen
threshold, explains them with SHAP, marks each `new` or `still_at_risk` against the
previous run, and appends the run to the alert log. The second reads that log and
delivers it to a person.

On a schedule:

```cron
# 09:00 every weekday
0 9 * * 1-5  cd /path/to/EO-Churn-Edu && .venv/bin/python -m pipeline.daily_pipeline && .venv/bin/python scripts/send_daily_alerts.py
```

Two commands joined with `&&`, deliberately: no alert goes out if the run failed. A
pipeline that silently scored nothing must not produce a cheerful "bugün risk
altında öğrenci yok" message.

`status` is defined against the **previous recorded run**, which is why
`POST /run-daily-pipeline` must not be called to populate a dashboard — every call
creates a new "previous run". Use `GET /students` for display.

---

## Notifications

`scripts/send_daily_alerts.py` reads the alert log (not the model — the run must
already have happened) and delivers a message written for a mentor, not an
engineer:

```
EO-Churn — Günlük Risk Uyarısı
11.09.2026 · 8 öğrenci risk altında, 3 tanesi yeni.

YENİ RİSKLİ ÖĞRENCİLER

1. STU300025 — ayrılma olasılığı %65
   - Son iletişimden bu yana (gün): 35 — riski artırıyor
   - Memnuniyet puanı (1-5): 1.51 — riski artırıyor
   - Aylık mentor görüşme sayısı: 3.4 — riski artırıyor
```

Students flagged for the **first time** get the full detail; ones already flagged
in the previous run are summarised in a single line, so a daily message does not
become the same eight names every morning.

The Turkish labels come from `FEATURE_LABELS` in `config.py` — one table, replaced
per client along with `FEATURES`. The values next to each reason are joined from
today's student data; a missing one shows as `veri yok` rather than failing the
message.

| Channel | Needs |
|---|---|
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `email` | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TO` — Gmail needs an App Password, not your login password |
| `webhook` | `ALERT_WEBHOOK_URL` |

### Telegram setup

```bash
python scripts/telegram_setup.py          # list the chats the bot can see
python scripts/telegram_setup.py --test   # send a test message
```

1. Message `@BotFather` in Telegram, send `/newbot`, copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. Open your new bot and send it `/start`.
3. Run `python scripts/telegram_setup.py` — it prints the chat id to put in
   `TELEGRAM_CHAT_ID`.
4. `python scripts/telegram_setup.py --test` to confirm.

Step 2 is the one everybody skips: **a bot cannot message someone who has not
messaged it first.** That is exactly what `chat not found` means.

### Safety behaviour

`NOTIFY_CHANNELS` is empty by default, so on a development machine the alert is
only printed — a test run cannot message a real mentor by accident. `--dry-run`
shows exactly what would be sent.

One channel failing does not stop the others and never fails the daily run: the
scoring and the alert-log write are already done by the time this runs. Each
channel's outcome is printed and the script exits non-zero if any failed, so a
scheduler can tell.

**It refuses to send in two cases**, both on purpose:

- the alert log has **no runs at all** — "no students at risk today" and "the
  pipeline never ran" are completely different facts, and only one is good news;
- the most recent run is **older than `--max-age-hours`** (default 24) — if the
  09:00 job failed, yesterday's alerts must not go out looking like today's.

Both exit 1 with an explanation; `--force` overrides either.

---

## The API

```bash
uvicorn api.main:app --reload
```

Interactive docs at <http://127.0.0.1:8000/docs>. Full request/response detail is
in **`API_CONTRACT.md`** — that is the document to hand to whoever builds the
frontend.

| Endpoint | Writes? | Purpose |
|---|---|---|
| `GET /health` | no | `ok`, or `degraded` with the reason if the model failed to load |
| `GET /students?threshold=` | **no** | score everyone, return those at or above the threshold — what a dashboard should call |
| `GET /predict/{student_id}` | no | one student: probability, reasons, and the feature values behind them |
| `POST /predict` | no | score a raw student payload that is not in the daily data |
| `POST /run-daily-pipeline` | **yes** | perform the day's run and record it |
| `GET /metrics` | no | the loaded model's metadata and metrics |

Every endpoint except `/health` requires the `X-API-Key` header when `API_KEY` is
set. Unset disables auth and the server logs a warning at startup.

The distinction that matters: `GET /students` and `POST /run-daily-pipeline`
compute the same thing, but only the second one records a run. Calling the second
one to fill a page corrupts the `new` / `still_at_risk` history.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

77 tests. The 9 database tests skip unless you give them a throwaway database:

```bash
createdb eo_churn_test
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/eo_churn_test pytest
```

It must be a throwaway one — every test truncates both tables. The variable is
deliberately not `DATABASE_URL`, so running `pytest` in a shell configured for
development can never write to the database you actually use.

Everything else in the suite is pinned to the CSV backend by an autouse fixture in
`tests/conftest.py`, whatever `DATA_SOURCE` says in your `.env`. A test run must
not change behaviour with the environment, and must never touch a real database or
send a real notification.

---

## Verifying the backend

`scripts/verify_backend.py` is the "does this thing actually work?" command. It
runs the real code paths the API uses and prints a pass/fail line per step:

```
[1/8] Environment      DATA_SOURCE, DATABASE_URL, auth
[2/8] Model            loads the model, calibrator and threshold
[3/8] Data source      reads today's students, validates them
[4/8] CSV/DB parity    (db mode) the DB returns exactly what the CSV does
[5/8] Scoring          scores, and proves nothing was written
[6/8] Daily run        (--write) records a run, checks the status values
[7/8] Notifications    builds the alert message; never sends
[8/8] API              /health, /students, /metrics, /predict/{id}
```

```bash
python scripts/verify_backend.py               # current DATA_SOURCE, writes nothing
python scripts/verify_backend.py --source db   # force the database backend
python scripts/verify_backend.py --write       # also record a real daily run
python scripts/verify_backend.py --train       # retrain first, then check everything
```

Exit code is 0 only if every step passed, so it also works as a smoke test in a
script. Without `--write` nothing is inserted into `alerts` or appended to
`daily_alerts.csv` — step 5 actively asserts that scoring had no side effects, and
step 7 builds the message without touching a channel.

### The whole chain from scratch

```bash
pip install -r requirements.txt
python running_train_pipeline.py        # 1. train -> saved_models/

createdb eo_churn                       # 2. create the database
# put DATABASE_URL in .env, then:
python scripts/init_db.py               #    apply the schema
python scripts/load_daily_students.py   # 3. CSV -> daily_students

# 4. set DATA_SOURCE=db in .env, then:
python scripts/verify_backend.py --write

python -m pipeline.daily_pipeline       # 5. the daily run
python scripts/send_daily_alerts.py     #    notify
uvicorn api.main:app --reload           # 6. serve
```

Steps 1–3 are one-time. The day-to-day loop is: refresh `daily_students` (3), run
the pipeline (5), notify.

---

## Troubleshooting

**`psql: command not found`**
The Postgres CLI tools are not on your PATH. On macOS with the EDB installer they
live in `/Library/PostgreSQL/<version>/bin`. Add that to your shell profile — or
skip it entirely: `python scripts/init_db.py` does the schema work through the
same driver the application uses and needs no `psql`.

**`password authentication failed for user "<your-mac-username>"`**
`psql` defaults to your OS username. The database user is `postgres`:
`psql -U postgres -d eo_churn`.

**`database "..." does not exist`**
Run `python scripts/init_db.py` — it lists the databases that *do* exist on that
server, which usually shows the typo (or that `DATABASE_URL` points at a different
Postgres instance than you expected). `--create-db` creates it.

A name created from a GUI may be quoted and case-sensitive — `"Eo-Churn"` is a
different database from `eo_churn`, and needs double quotes in every SQL statement
forever. Rename it once instead:
`psql -U postgres -d postgres -c 'ALTER DATABASE "Eo-Churn" RENAME TO eo_churn;'`
(close any open connections to it first).

**DataGrip still shows the old database name**
It caches the list. Right-click the data source → *Refresh*; if it persists,
*Properties → Schemas*, refresh the list there, and check the *Schema pattern*
field at the bottom for the stale name.

**`relation "daily_students" does not exist`**
The schema was never applied to this database: `python scripts/init_db.py`.

**`chat not found` from Telegram**
The token is fine (a bad token gives 401, not 400) — the chat id is wrong, or you
have never messaged the bot. Open it in Telegram, send `/start`, then
`python scripts/telegram_setup.py`.

**The alert says there are no students at risk, but nothing ran**
It no longer does — `send_daily_alerts.py` refuses to send when the alert log has
no runs, or when the last one is older than 24 hours. If you see that refusal, run
`python -m pipeline.daily_pipeline` first.

**Tests fail with foreign-key errors or `still_at_risk` where `new` was expected**
That was a real bug: the suite followed `DATA_SOURCE` from `.env` and reached a
live database. Fixed by an autouse fixture in `tests/conftest.py`. If you see it
again, that fixture is not being applied.

**`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`**
Harmless today. When Starlette drops support it will break the test suite —
swapping `httpx` for `httpx2` in `requirements-dev.txt` is the one-line fix.

**`InconsistentVersionWarning` when unpickling the calibrator**
The calibrator was saved by a different scikit-learn version than the one you have.
It still loads. Retrain (`python running_train_pipeline.py`) to clear it.

---

## Onboarding a new client

The system is meant to be re-pointed at a new dataset, not rewritten. What changes:

1. **`config.py`** — `TRAIN_DATA_PATH` / `DAILY_DATA_PATH`, then `FEATURES`,
   `CAT_COLS`, `STUDENT_INFO`, `FEATURE_BOUNDS` and `FEATURE_LABELS` to match the
   new columns.
2. **`src/data/features.py`** — the recipe: which columns get a missing-flag, which
   are imputed, and the column the medians are grouped by.
3. **`DECISION_COST`** — the real relative cost of a false alarm versus a missed
   churn *for this client*. This drives threshold selection and is a business
   conversation, not a hyperparameter.
4. Rebuild and retrain: `python scripts/build_training_data.py`, then
   `python running_train_pipeline.py`.

No other file needs to change. The database schema does not change either —
features live in a JSONB column precisely so that a different column list is a
config edit rather than a migration.

---

## Known limitations

- **The model is trained on synthetic data.** Every metric, and the chosen
  threshold, is a placeholder until real client data arrives.
- **No scheduler.** The daily run is a cron line you have to add; nothing in the
  repo runs itself yet.
- **Single-container Docker only.** No `docker-compose.yml` — no Postgres service,
  no scheduler service.
- **No endpoint reads the `alerts` table.** History and `status` are recorded but
  not exposed over the API, so a dashboard cannot show them yet.
- **No model version on alert rows.** Which model produced a given alert is not
  recorded, so old alerts cannot be traced back to the model that made them.
- **`GET /students` re-scores everything on every call.** Fine at this size
  (~70 ms for 25 students), slow at ten thousand.
