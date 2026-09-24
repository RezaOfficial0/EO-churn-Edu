# EO Churn Early-Warning System

Predicts which mentoring students are likely to leave the programme, explains
**why** for each one, and delivers that to a mentor every morning — so someone can
reach out before the student is gone.

| | |
|---|---|
| **Model** | CatBoost classifier, calibrated with Platt scaling (logistic / "sigmoid") so `churn_probability` is a real probability, not a ranking score |
| **Explanations** | top-N SHAP contributions per student, in plain Turkish |
| **Serving** | FastAPI — single prediction, lookup by `student_id`, read-only listing. The daily batch run is a scheduled command, not an endpoint — a compose service runs it |
| **Storage** | CSV files or PostgreSQL, switched by one environment variable |
| **Delivery** | Telegram, email (SMTP), or webhook — with a separate operator channel for when a run fails |
| **Schedule** | a compose service runs the daily chain at `RUN_AT` and alerts us if a day goes by without a successful run |

> **The numbers in this repo come from a synthetic dataset.** They demonstrate
> that the *system* works; they say nothing about how well churn can actually be
> predicted for a real client. `model_meta.json` and `GET /metrics` both carry an
> `is_synthetic_data` flag as a reminder.
>
> | | value |
> |---|---|
> | ROC-AUC | 0.718 |
> | PR-AUC (average precision) | 0.512 |
> | Brier score | 0.173 |
> | precision@20 / lift@20 | 0.75 / 2.76 |
> | chosen threshold | 0.29 |
> | precision / recall **at that threshold** | 0.430 / 0.571 |
> | confusion matrix (test, n=677) | `[[354, 139], [79, 105]]` |
>
> In plain words at the shipped operating point: roughly **57% of churners are
> caught** and roughly **57% of alerts are false alarms**. The ranking metrics
> alone would be a flattering way to describe that, so both are printed here.
> Every number above is read from `saved_models/model_meta.json`; if they
> disagree, the file is right and this table is stale.

---

## Quickstart

**Python 3.13** (what CI and the Docker image use; `pandas==3.0.5` /
`numpy==2.5.2` will not install on much older interpreters).

A fresh clone already ships a trained model and sample data — `saved_models/` and
`data/*.csv` are committed on purpose — so this is enough to see it working:

```bash
pip install -r requirements.txt
cp .env.example .env
python scripts/verify_backend.py     # proves the whole chain works, writes nothing
uvicorn api.main:app --reload        # http://127.0.0.1:8000/docs
```

That runs on CSV files. To use PostgreSQL instead, see
[Data sources](#data-sources-csv-or-postgres). If you would rather not install
Python and Postgres at all, skip to [Running with Docker](#running-with-docker).

---

## Running with Docker

The manual path above is for developing. For a demo — or for any machine that
should not need Python, Postgres and npm installed — the whole stack comes up
with one command:

```bash
cp .env.docker.example .env.docker
./scripts/demo_up.sh
```

That builds the images, starts Postgres, creates the schema, loads the daily
data, runs one daily pipeline, waits until the API reports healthy, and prints
the URLs. The dashboard list is **already populated** when you open it — no
manual step in between. Everything the script runs is idempotent, so running it
again is safe.

| | |
|---|---|
| Dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Postgres | `localhost:5432` (change with `DB_PORT`) |

### What is in the stack

| Service | Role |
|---|---|
| `db` | postgres:18-alpine, data kept in the `pgdata` volume |
| `init` | schema + migrations, daily data, one pipeline run — then exits |
| `api` | uvicorn, starts only after `init` has succeeded |
| `scheduler` | the daily run on a timer: pipeline `&&` alerts at `RUN_AT`, plus the heartbeat. Same image as `api`. See [Zamanlama](#zamanlama) |
| `dashboard` | the React app built and served by nginx (separate compose file) |

`init` exists so that `up` alone produces a working demo. `api` waits for it via
`depends_on: condition: service_completed_successfully`, and `init` in turn waits
for `db` to answer `pg_isready` — not merely to be "running", which is the gap
that caused the connection errors in the manual setup.

The dashboard lives in its own repository, so it is defined in a second file and
is only added when that repo sits next to this one:

```
Eo-Churn-FullProject/
  EO-churn/                      <- you are here
  Eo-Churn-Dashboard-demo-Edu/
```

`demo_up.sh` detects that and includes `docker-compose.dashboard.yml`
automatically; without it you get backend only.

### Settings

`.env.docker` is separate from `.env` and must stay that way: `.env` points at
`localhost` for venv development, `.env.docker` points at the `db` service inside
the compose network. Both carry secrets and neither is committed — the
`.env.docker.example` template is.

| Variable | Why you would change it |
|---|---|
| `DB_PORT` | a local Postgres already holds 5432 — set `5433` |
| `API_PORT` | something else holds 8000. Only the host-side publish; the dashboard no longer cares (D-09) |
| `DASHBOARD_PORT` | something else holds 5173 |
| `VITE_API_BASE` | the base the *browser* uses. `/api` — same origin, proxied by the dashboard's own nginx. Still baked in at **build** time, so change it and rebuild with `--build`, but it no longer has to track `API_PORT` |
| `API_UPSTREAM` | where the dashboard's proxy finds the API. `http://api:8000` — the compose service name, correct here precisely because nginx runs in the container and not in the browser |
| `ALLOWED_ORIGINS` | CORS allow-list. The dashboard is same-origin now, so this only matters for a browser client served from somewhere else |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | the template ships `postgres` / `eochurn` — **a demo password, change it for anything that is not your own laptop** |
| `API_KEY` | empty in the template. The API then **refuses to start** unless `EOAI_ALLOW_NO_AUTH=1` is also set, which the template does for the local demo — and which is only honest because compose publishes the API port on `127.0.0.1` only. Reaching it from another machine means setting a real key. This one value drives **both ends**: the API requires it and the dashboard's nginx injects it, so setting it needs no dashboard change and the key never reaches a browser |
| `NOTIFY_CHANNELS` | empty prints to stdout only; `telegram` actually sends |
| `RUN_AT`, `SCHEDULER_TIMEZONE`, `RUN_DAYS` | when the daily run happens (default 09:00 Europe/Istanbul, every day) — [Zamanlama](#zamanlama) |
| `OPS_TELEGRAM_CHAT_ID`, `OPS_ALERT_WEBHOOK_URL` | where a **failed** run is reported. Ours, not the customer's channel. Empty means the failure is only in the log and in `docker compose ps` |
| `SCHEDULER_HEARTBEAT_HOURS` | no successful run in this many hours → an ops alert (default 26) |

Values containing spaces must be quoted (`NOTIFY_TITLE="EO-Churn — Risk"`).
Compose parses the file itself via `--env-file`, and `demo_up.sh` /
`demo_reset.sh` read the few values they need as plain text rather than sourcing
the file as shell — so a stray space cannot be executed as a command. Quote
anyway; anything else that reads the file may not be as careful.

Changing the API port is now a single edit — `VITE_API_BASE` is a path, so it no
longer has to follow:

```bash
sed -i '' 's|^API_PORT=.*|API_PORT=8010|' .env.docker
./scripts/demo_up.sh
```

### Turning the API key on

```bash
sed -i '' 's|^API_KEY=.*|API_KEY=<a long random string>|' .env.docker
sed -i '' '/^EOAI_ALLOW_NO_AUTH=/d'                        .env.docker
./scripts/demo_up.sh
```

That is the whole change. The API starts requiring `X-API-Key`; compose passes the
same `API_KEY` into the dashboard container, where nginx adds the header to every
proxied request (D-09). The key is a **runtime** variable of that container, so it
is in neither the dashboard bundle nor the image history, and the browser never
holds it. The dashboard build itself does not change.

### Before a demo

```bash
./scripts/demo_reset.sh
```

The `status` field (`new` / `still_at_risk`) is computed against the *previous*
run. Without a reset, a second demo on the same day shows "0 new" and a message
with no reasons in it.

The reset truncates `alerts`, reloads the daily data, records a **synthetic
"yesterday" run** (`scripts/seed_demo_history.py`) and then today's real run. The
synthetic run exists because two parts of the message only appear when there is
an earlier run to compare against — the "already flagged yesterday" summary and
its trend arrows (`%56 ↑ (önceki %42)`). With a single run neither renders.
About 60% of today's at-risk students are carried into "yesterday", mostly at a
lower probability, so the demo shows both new students in detail and repeat
students with rising risk. The choice is seeded, so every reset gives the same
demo.

`./scripts/demo_reset.sh --no-history` skips the synthetic run: one run only,
every student new, no arrows.

`seed_demo_history.py` fabricates history. It refuses to run against an alert log
that already has runs unless given `--force`, and must never be pointed at a
customer's data.

To preview the notification without sending anything:

```bash
./scripts/demo_message.sh
```

That is the `--dry-run` path, so nothing is sent even with
`NOTIFY_CHANNELS` filled in.

### Backing up

There is no automatic backup. `daily_students` can be rebuilt from the CSV, but
**`alerts` cannot be rebuilt from anything** — it is the only record of who was
flagged, when, with what probability and for what reason, and it is the evidence
base for every claim the product makes. Before any `down -v`, and on any machine
holding data you care about:

```bash
docker compose --env-file .env.docker exec -T db \
  pg_dump -U postgres -d eo_churn > backup-$(date +%F).sql
```

Restore into an empty database with:

```bash
docker compose --env-file .env.docker exec -T db \
  psql -U postgres -d eo_churn < backup-2026-09-19.sql
```

### Stopping

```bash
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.dashboard.yml down     # keeps the data
docker compose --env-file .env.docker -f docker-compose.yml -f docker-compose.dashboard.yml down -v  # wipes it; next up rebuilds from scratch
```

`down -v` deletes the `pgdata` volume, which means the whole alert history. Take
a dump first unless you are certain the data is disposable.

### When it does not come up

| Symptom | Cause |
|---|---|
| `docker version` hangs after the Client block | Docker Desktop's engine is not running — restart Docker Desktop |
| `bind: address already in use` | port taken; change `DB_PORT` / `API_PORT` / `DASHBOARD_PORT` |
| dashboard loads but the list is empty | `GET /metrics` failed, so the dashboard has no threshold and deliberately renders nothing. Check `docker compose logs api` |
| dashboard shows 401 "missing or invalid X-API-Key" | the proxy's `API_KEY` and the API's `API_KEY` disagree — both come from `.env.docker`, so this usually means the dashboard container was not recreated after the file changed (`up -d` again) |
| dashboard says "API'ye ulaşılamıyor" while `curl` works | the dashboard's nginx cannot reach `API_UPSTREAM`. It resolves that hostname when it loads its config, so an API container replaced with a new IP needs `docker compose restart dashboard` |
| dashboard shows an orange "Yapılandırma hatası" banner | it was built without `VITE_API_BASE`, or with an absolute `http://` address while the page is served over HTTPS. Rebuild with `--build-arg VITE_API_BASE=/api` |
| `db` restarts in a loop, log mentions "unused mount/volume" | a `pgdata` volume created by an older config — `down -v` and start again (Postgres 18+ wants the mount at `/var/lib/postgresql`, not `/var/lib/postgresql/data`) |
| `demo_up.sh` times out | `docker compose --env-file .env.docker logs init api` |

---

## Command reference

Every command in the project, in the order you would meet them.

### Setup

| Command | What it does |
|---|---|
| `pip install -r requirements.txt` | runtime dependencies |
| `pip install -r requirements-dev.txt` | test dependencies (pytest) on top of the runtime ones |
| `cp .env.example .env` | create your local settings file — every value is optional |

### Training

| Command | What it does |
|---|---|
| `python running_train_pipeline.py` | train, calibrate, pick the threshold, evaluate, write `saved_models/`. Required before serving: without `saved_models/calibrator.joblib` the API reports `degraded` and refuses to score, and the daily pipeline stops (B-20) |
| `python scripts/build_training_data.py` | writes the engineered frame to `data/updated_data.csv` **for inspection only** |
| `python scripts/compare_feature_sets.py` | train several feature-set variants into a temp dir and print a comparison table; touches nothing in `saved_models/` |

Training always reads CSV and ignores `DATA_SOURCE`.

`running_train_pipeline.py` reads the **raw** export (`config.RAW_DATA_PATH`) and
runs the feature-engineering recipe itself. It does **not** read
`data/updated_data.csv`, so `build_training_data.py` is not a step you have to run
before training — it exists so you can open the engineered frame in a spreadsheet
and see what the recipe produced.

### Database (only for `DATA_SOURCE=db`)

| Command | What it does |
|---|---|
| `createdb eo_churn` | create the database (or do it in DataGrip / pgAdmin) |
| `python scripts/init_db.py` | apply the migrations this database has not run yet, then verify |
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
| `python scripts/scheduler.py` | the scheduler loop — pipeline `&&` alerts at `RUN_AT`, every day (what the compose `scheduler` service runs) |
| `python scripts/scheduler.py --once` | run the chain now and exit; non-zero if it failed |
| `python scripts/scheduler.py --next 5` | print the next 5 due times, run nothing |
| `python scripts/scheduler.py --healthcheck` | non-zero if the last run failed or none succeeded inside the heartbeat window |

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
| `./scripts/demo_up.sh` | build and start the whole stack, wait until the API is healthy |
| `./scripts/demo_reset.sh` | back to a clean demo state (synthetic yesterday + today) — do this before every demo |
| `./scripts/demo_message.sh` | print the daily alert message without sending it |
| `docker compose --env-file .env.docker ps` | what is running |
| `docker compose --env-file .env.docker logs -f api` | follow the API log |
| `docker compose --env-file .env.docker logs -f scheduler` | follow the daily run / heartbeat log |
| `docker compose --env-file .env.docker down` | stop, keep the data |
| `docker compose --env-file .env.docker down -v` | stop and wipe the database |

See [Running with Docker](#running-with-docker) for the details.

---

## How it works

Three flows. They share the feature-engineering recipe and nothing else, which is
what keeps training reproducible and serving fast.

**1. Training** — occasional, manual, always CSV:

```
data/mentorluk_churn_veriseti.csv        (config.RAW_DATA_PATH)
  -> validate            reject bad data before it reaches the model
  -> feature engineering missing-flags, then group-median imputation
  -> train/val/test split
  -> CatBoost + early stopping           (on the validation set)
  -> Platt / sigmoid calibration         (on the validation set)
  -> threshold selection                 (minimises DECISION_COST, on the same set)
  -> evaluate + save                     saved_models/ + model_meta.json
```

Note that the validation set does three jobs — early stopping, calibration and
threshold selection. That is a known weakness, not a design choice: the operating
point it produces is measurably optimistic (see
[Known limitations](#known-limitations)).

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
  updated_data.csv              the engineered frame, for inspection only - NOT read by training
  daily_data.csv                sample "today's students" for the daily run
  daily_alerts.csv              the alert log in CSV mode (gitignored, regenerated)

db/
  schema.sql                  the current schema, readable in one place (documentation)
  migrations/                 NNN_*.sql, each run once per database (000 = baseline)

src/
  data/loader.py              CSV and Postgres backends for daily data + the alert log,
                              switched by DATA_SOURCE (training stays CSV-only)
  data/features.py            THE feature-engineering recipe (nulls -> flags + impute)
  data/validation.py          reject bad data before it reaches the model
  data/preprocess.py          select FEATURES, cast categoricals, train/val/test split
  serialization.py            to_external(): the one conversion every API response goes through
  logging_setup.py            one-time logging configuration + the per-request id
  scheduling.py               WHEN the daily run is due (pure: RUN_AT + tz + RUN_DAYS ->
                              the next instant) and the last-success/failure state file
  model/model.py              build the CatBoost classifier
  model/train.py              fit with early stopping on the validation set
  model/calibrate.py          Platt (sigmoid) and isotonic calibrators; sigmoid is the one in use
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
    ops.py                    the OPERATOR channel: a failed run goes to us, not to
                              the customer's group (OPS_TELEGRAM_CHAT_ID / webhook)

pipeline/
  training_pipeline.py        the training flow above
  daily_pipeline.py           score_students() (pure) + log_alerts() (writes)

api/main.py                   FastAPI app

scripts/
  build_training_data.py      raw CSV -> data/updated_data.csv (inspection only)
  init_db.py                  run pending db/migrations/, tracked (no psql needed)
  load_daily_students.py      CSV -> the daily_students table
  send_daily_alerts.py        deliver today's alert to Telegram / email / webhook
  scheduler.py                the daily run on a timer: pipeline && alerts at RUN_AT,
                              plus the heartbeat (the compose `scheduler` service)
  telegram_setup.py           find the chat id for .env, prove the bot can reach it
  verify_backend.py           run the whole chain end to end and report what works
  test_api.py                 live smoke test (needs a running server; read-only by default)
  compare_feature_sets.py     offline feature-set comparison (writes to a temp dir only)
  demo_up.sh                  bring the whole Docker stack up
  demo_reset.sh               back to a clean demo state, before a demo
  seed_demo_history.py        DEMO ONLY: a synthetic "yesterday" run, so trends render
  demo_message.sh             print the daily message without sending it

tests/                        pytest suite (the 9 database tests skip without TEST_DATABASE_URL)

state/                        scheduler state: last successful / failed run (gitignored;
                              a named volume in compose, see Zamanlama)

metrics/                      per-run training metrics (gitignored, regenerated)
saved_models/                 the served model, calibrator and model_meta.json (committed)

Dockerfile                    the API image
docker-compose.yml            db + init + api + scheduler
docker-compose.dashboard.yml  the dashboard service (needs the sibling repo); also
                              wires its /api reverse proxy to this API
.github/workflows/ci.yml      CI: compileall + pytest (no Postgres, so DB tests skip)

RnD/                          EXPERIMENTAL, UNSUPPORTED. Not imported by anything,
                              not covered by CI, and model_2.py does not currently
                              import at all. Do not treat its numbers as results.
notebooks/                    exploratory notebook + its own copy of a split dataset
```

---

## Configuration

Everything you would tune per deployment lives in `config.py`:

| Setting | Meaning |
|---|---|
| `RAW_DATA_PATH` | the export training actually reads |
| `TRAIN_DATA_PATH` | where `build_training_data.py` writes the engineered frame (inspection only) |
| `DAILY_DATA_PATH`, `DAILY_ALERTS_PATH` | the daily pipeline's CSV input and alert log |
| `MODEL_PATH`, `MODEL_META_PATH`, `CALIBRATOR_PATH`, `METRICS_DIR` | model artefact locations, anchored to the repo root |
| `STUDENT_INFO` | id columns passed through to API responses, never fed to the model |
| `FEATURES` | the exact columns the model is trained and served on |
| `CAT_COLS` | which of `FEATURES` are categorical |
| `TARGET_FEATURE` | the label column (`"churn"`) |
| `MODEL_PARAMS` | CatBoost `iterations` / `depth` / `learning_rate` |
| `CALIBRATION_METHOD` | `"sigmoid"` (Platt, in use) or `"isotonic"` — the comment above it records why isotonic was rejected on this data |
| `PLAN_MONTHS` | months per plan name, used to turn `monthly_fee_try` into `monthly_value_try`. **Per client**, and currently Turkish plan names |
| `DECISION_COST` | relative cost of a false alarm vs. a missed churn — drives threshold selection |
| `PRECISION_AT_K` | how many students a mentor can realistically contact per run |
| `FEATURE_BOUNDS` | accepted min/max per numeric input (API input validation) |
| `CATEGORICAL_LEVELS` | the accepted values of each categorical input. **Per client.** CatBoost hashes an unseen category instead of refusing it, so this list is the only thing that rejects `{"plan_type": "banana"}` |
| `MAX_CATEGORY_LENGTH` | longest allowed category name, checked on `CATEGORICAL_LEVELS` itself |
| `INTEGER_FEATURES`, `FLAG_FEATURES` | which numeric inputs are whole numbers (counters, day counts) and which are 0/1 indicators — `POST /predict` refuses a float in either |
| `MAX_NULL_RATIO_PER_COLUMN` | a single column above this fraction of nulls fails validation. **Training only** — the daily pipeline passes `1.0`, i.e. disables it, and relies on `require_no_nulls` after imputation instead |
| `SHAP_TOP_N_FEATURES` | how many reasons to return per student |
| `FEATURE_LABELS` | Turkish label per feature, used in the daily alert message |

`config.py` runs `_validate_feature_config()` at import: it checks `FEATURES`
against `CAT_COLS`, `FEATURE_BOUNDS`, `FEATURE_LABELS`, `CATEGORICAL_LEVELS`,
`INTEGER_FEATURES`, `FLAG_FEATURES` and `STUDENT_INFO`, and
refuses to load on a duplicate, a stale bound, a missing label or a target column
that leaked into the feature list. If you are onboarding a new dataset, that
function's error messages are the fastest way to find what you forgot. It checks
membership, **not order** — reordering `FEATURES` without retraining will silently
misalign CatBoost's categorical indices.

Everything environment-specific goes in `.env` (see `.env.example` — every variable
is optional and documented there):

| Variable | Purpose |
|---|---|
| `DATA_SOURCE` | `csv` (default) or `db` |
| `DATABASE_URL` | Postgres connection string, required for `db` |
| `API_KEY` | shared secret for the `X-API-Key` header; unset means the API refuses to start. The dashboard container reads the same variable and injects the header server-side |
| `API_BIND_HOST` | the address the API is reachable on; unset is treated as public |
| `EOAI_ALLOW_NO_AUTH` | `1` starts without a key on a loopback `API_BIND_HOST` (local development only) |
| `ALLOWED_ORIGINS` | CORS origins allowed to call the API; not needed by the same-origin dashboard |
| `NOTIFY_CHANNELS` | `telegram`, `email`, `webhook` — comma-separated, empty = print only |
| `NOTIFY_TITLE` | heading at the top of every alert message |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram delivery |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`, `SMTP_STARTTLS` | email delivery |
| `ALERT_WEBHOOK_URL` | Slack / Discord incoming webhook |
| `TEST_DATABASE_URL` | read only by the test suite — a **throwaway** database for the 9 DB tests, which truncate both tables before every test. Deliberately not `DATABASE_URL` |

`.env` is gitignored. Never commit it — it holds the database password, the API key
and the bot token.

---

## Data sources: CSV or Postgres

By default the daily pipeline reads `data/daily_data.csv` and appends to
`data/daily_alerts.csv`. Setting `DATA_SOURCE=db` swaps both for Postgres tables.
**Nothing changes in the HTTP surface** — same endpoints, same response shapes.
Training is unaffected either way: it always reads CSV.

Two differences below the API, worth knowing before you build on the alert log:
the CSV log carries an extra `run_date` column and **cannot** hold
`top_reasons_detail` (the message layer re-parses the display string in CSV mode),
and the DB has a unique index on `(run_at, student_id)` while the CSV relies on an
exclusive `flock` around each append — so two pipeline runs at the same moment no
longer splice their rows together in either backend, but only Postgres rejects a
genuine duplicate.

The switch exists so the two can run side by side. If the database is down,
`DATA_SOURCE=csv` still works; if a CSV gets corrupted, `db` still works. It is a
fallback, not a migration you have to finish.

### Setting up Postgres

```bash
createdb eo_churn                      # or create it in DataGrip / pgAdmin

# put DATABASE_URL in .env, then:
python scripts/init_db.py              # pending migrations, then verify
python scripts/load_daily_students.py  # CSV -> daily_students

# finally, in .env:
DATA_SOURCE=db
```

`init_db.py` connects through the same driver the application uses, so it needs no
`psql` on your PATH — if it works, the pipeline's connection works. It runs every
migration this database has not recorded yet, then verifies the result. Safe to
re-run: with nothing pending it changes nothing. `--verify` checks without
changing anything, and also reports pending migrations.

Creating the *database itself* is opt-in (`--create-db`) rather than automatic: a
typo in `DATABASE_URL` would otherwise produce a mysteriously empty database
instead of an error. Without the flag, a missing database is reported along with
the databases that do exist on that server — usually enough to spot the typo.

Re-run `load_daily_students.py` whenever the day's data changes. Students are
matched on `student_id` and updated in place, so running it twice is harmless. The
CSV is validated first, so a file with a missing column, a stray extra column or a
duplicate id is rejected rather than half-loaded.

**It never removes anybody.** A student who is in `daily_students` but absent from
today's file keeps their last known feature values and keeps being scored every
day — because `alerts.student_id` is a foreign key and deleting the row would take
their alert history with it. So a cohort that finished the programme will go on
producing alerts, with frozen inputs, forever. There is no `--replace`, no
`is_active` flag and no archival path yet; until there is, a long-running
deployment needs someone to prune by hand.

### The schema

| Table | What it holds |
|---|---|
| `daily_students` | today's students — id columns natively, every feature in a `features` JSONB blob |
| `alerts` | one row per at-risk student per run, append-only (the `daily_alerts.csv` equivalent) |

`features` is JSONB rather than 24 typed columns because the feature list is per
client: re-pointing the system at a new dataset means editing `config.FEATURES`,
and typed columns would make that a migration every time. `loader.py` coerces the
values back to numeric on read, so nothing downstream can tell the difference.

**Migrations.** `init_db.py` runs the files in `db/migrations/` in number order
and records each one in a `schema_migrations` table, so every migration runs
**once** per database. `000_baseline.sql` is the schema as it stood when tracking
started; a database created before that is adopted by the same command (the
baseline is all `IF NOT EXISTS`, `001`/`002` are guarded). The whole run is one
transaction under an advisory lock: a failing migration rolls back everything that
run applied, and two `init` containers cannot migrate at the same time.

`db/schema.sql` is the readable current shape, not something `init_db.py` applies.
To change the schema:

1. add `db/migrations/NNN_short_name.sql` (next number, lowercase, no gaps),
2. make the same change in `db/schema.sql`,
3. run `pytest` with `TEST_DATABASE_URL` set — `tests/test_migrations.py` fails if
   the two describe different databases, or if a file is misnamed.

Never edit a migration that has shipped: databases that already ran it will not run
it again, and would silently differ from new ones.

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

Two commands joined with `&&`, deliberately: no alert goes out if the run failed. A
pipeline that silently scored nothing must not produce a cheerful "bugün risk
altında öğrenci yok" message.

On a schedule: nothing to add by hand — see [Zamanlama](#zamanlama) below. The
`scheduler` service runs exactly these two commands, in this order, with that same
`&&`.

`status` is defined against the **previous recorded run**, which is why the run is a
scheduled command and not an endpoint: anything that could trigger it over HTTP —
a double-clicked dashboard button included — would create a new "previous run" and
mark every student `still_at_risk`. `POST /run-daily-pipeline` was removed for that
reason (B-08). Use `GET /students` for display.

---

## Zamanlama

Günlük koşuyu `docker-compose.yml` içindeki **`scheduler`** servisi çalıştırıyor.
Elle cron satırı eklemek yok; eski README'deki crontab örneği bu servisle
değiştirildi (B-14).

```bash
docker compose --env-file .env.docker up -d            # scheduler dahil
docker compose --env-file .env.docker logs -f scheduler
docker compose --env-file .env.docker ps               # (healthy) / (unhealthy)
```

Servis `api` ile **aynı imajı** kullanıyor (aynı model dosyaları, aynı `config.py`)
ama ayrı bir container: API'nin restart'ı koşuyu kaçırtmıyor, koşunun CPU'su
isteklere yansımıyor. Veritabanına ve modele **doğrudan** gidiyor, HTTP'ye hiç
dokunmuyor — yani `API_KEY` / `EOAI_ALLOW_NO_AUTH` (B-07) bu servisi
ilgilendirmiyor.

### Ayarlar

| Değişken | Ne işe yarar |
|---|---|
| `RUN_AT` | koşu saati, `HH:MM` (varsayılan `09:00`). Anlamsız bir değer servisi **başlatmıyor** — kendi kafasına göre bir saat seçmesindense çıkması iyidir |
| `SCHEDULER_TIMEZONE` | saatin hangi dilimde okunacağı (varsayılan `Europe/Istanbul`). Container'ın saati UTC olduğu için bu satır şart: dilimsiz `09:00`, mentora öğlen ulaşır |
| `RUN_DAYS` | günler, `Pzt=1 .. Paz=7`. Boş = her gün. Sadece hafta içi: `1-5` |
| `SCHEDULER_HEARTBEAT_HOURS` | bu kadar saattir başarılı koşu yoksa **bize** uyarı (varsayılan 26) |
| `SCHEDULER_RUN_TIMEOUT_SECONDS` | tek bir adımın üst süre sınırı (varsayılan 3600). Asılı kalan bir bağlantı, "ayakta ama hiçbir şey yapmıyor" hâlini üretir; timeout onu arızaya çeviriyor |
| `SCHEDULER_STATE_PATH` | son başarılı/başarısız koşu kaydı. Compose'da `scheduler_state` volume'u |
| `OPS_TELEGRAM_CHAT_ID` | arıza mesajının gideceği **bizim** sohbetimiz (aynı bot, farklı sohbet) |
| `OPS_ALERT_WEBHOOK_URL` | ya da bizim Slack/Discord webhook'umuz |

`RUN_DAYS=1-5` kullanıyorsan `SCHEDULER_HEARTBEAT_HOURS`'u ~74'e çek: cuma
koşusundan pazartesiye 72 saat var ve 24 saatlik pencere her pazartesi sabahı
arıza gibi görünür. Varsayılanın 26 saat olmasının sebebi de bu ailedendir: tam
24 saatlik pencere, 09:00'dan 09:00'a hiç pay bırakmıyor ve servisi 09:00'ı
geçtikten sonra yeniden başlattığın her seferde ilk sabah yanlış bir uyarı
üretirdi.

Saati kontrol etmenin en hızlı yolu — hiçbir şey çalıştırmadan sonraki koşuları
yazdırır:

```bash
docker compose --env-file .env.docker exec scheduler python scripts/scheduler.py --next 5
```

### Nasıl çalışıyor

`scripts/scheduler.py` sonraki koşu anını hesaplayıp o ana kadar **uyuyor**, sonra
zinciri çalıştırıyor. Cron yerine uyuyan bir döngü, çünkü: bütün zamanlama
`.env`'de üç değişken (ikinci bir crontab/ofelia yapılandırma dili yok), hesap tek
bir saf fonksiyon (`src/scheduling.py: next_run_at`) ve bu yüzden DST dahil
**test edilebilir** — 09:00'ı beklemeden. Container içindeki cron ise sessizdir:
kendi ortamı vardır, hatayı hiçbir yere postalamaz, yazım hatası olan satır hiç
ateşlenmez.

Uyuyan döngünün dürüst bedeli: container kapalıyken gelen saat **kaçar**. Makine
09:00'da kapalıysa, 09:15'te açıldığında koşu yapılmaz — yarının saati
hesaplanır. Yakalayan şey heartbeat'tir.

DST: koşu saati yerel duvar saatidir. Saatin geri alındığı gün aynı saat iki kez
yaşanır; ilk olanı seçiliyor, yani koşu **bir kez** yapılıyor. İleri alındığı gün
o saat hiç yaşanmıyorsa koşu o gün gerçek bir ana denk getirilip yine yapılıyor.
(Türkiye 2016'dan beri sabit UTC+3, yani varsayılan kurulumda bunların ikisi de
olmuyor — başka bir dilime geçince oluyor.)

### Koşu başarısız olursa

Zincir `&&` ile bağlı: `daily_pipeline` sıfır dönmezse `send_daily_alerts.py`
**hiç başlamıyor**. Müşteri yarım ya da yanıltıcı bir mesaj almıyor — "bugün risk
altında öğrenci yok" ile "koşu hiç olmadı" aynı şey değil ve ikincisi bir öğrenciye
mal olur.

Sırayla:

1. sonuç durum dosyasına yazılıyor (`last_failure_at`, hangi adım, çıktının sonu);
2. container log'una **büyük harflerle** düşüyor — ops kanalı olmasa bile;
3. `OPS_TELEGRAM_CHAT_ID` / `OPS_ALERT_WEBHOOK_URL` varsa **bize** mesaj gidiyor.
   Müşterinin kanalına değil: `NOTIFY_CHANNELS` ile hiç ilgisi yok. "KeyError in
   daily_pipeline" bir mentorun yapabileceği bir şey değil, ama ürüne olan güveni
   bitirecek türden bir mesajdır;
4. servis kendini **unhealthy** ilan ediyor, yani `docker compose ps` çıktısında
   görünüyor. Hiçbir ops kanalı ayarlı değilse arızayı görünür kılan tek şey bu
   satır ve log'dur — pilot öncesi en az birini ayarla, kimse log izlemiyor.

Hata mesajı `redact()`'ten geçiyor: bot token'ı, webhook adresi ya da bağlantı
dizesi ne log'a ne de Telegram'a düşüyor.

### Heartbeat

`SCHEDULER_HEARTBEAT_HOURS` (varsayılan 26) saattir **başarılı** koşu yoksa ops
kanalına uyarı gidiyor ve servis unhealthy oluyor. Bu, ürünün doğası gereği
görünmez olan tek arızayı yakalıyor: sistem üç gündür ölü olsa da, risk altında
kimsenin olmadığı sağlıklı bir gün gibi görünür. Kontrol açılışta da yapılıyor —
iki gün kapalı kalmış bir container bunu yarın 09:00'da değil, açılır açılmaz
söylüyor. Aynı pencere içinde uyarı tekrarlanmıyor, yoksa restart döngüsü her
yeniden başlayışta bir mesaj atar.

Son başarılı koşunun zamanı **`scheduler_state` volume'undaki küçük bir JSON
dosyasında** tutuluyor (`SCHEDULER_STATE_PATH`, imaj içinde `/app/state`).
Neden tabloda değil: şemada yalnızca `daily_students` ve `alerts` var; `alerts`
müşteriye ait, yalnızca eklenen bir geçmiş ve "koşu oldu" diyen uydurma bir satır
hem o geçmişi hem de `new` / `still_at_risk` karşılaştırmasını bozar. Bu kaydın
asıl yeri `runs` tablosu ve o **B-04**'ün işi. Volume adlandırılmış olduğu için
`up --build` ve restart dosyayı korur; `down -v` siler (o da veritabanıyla birlikte,
tutarlı).

### Elle çalıştırmak

| Komut | Ne yapar |
|---|---|
| `python scripts/scheduler.py` | döngü: sırası gelince zinciri çalıştırır (compose'un çalıştırdığı) |
| `python scripts/scheduler.py --once` | zinciri **şimdi** çalıştırır ve çıkar; başarısızsa 1 döner |
| `python scripts/scheduler.py --next 5` | sonraki 5 koşu anını yazar, hiçbir şey çalıştırmaz |
| `python scripts/scheduler.py --healthcheck` | son koşu başarısız ya da eskiyse 1 döner (compose healthcheck'i budur) |

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

The message is capped: at most 10 students in full detail and 15 repeat lines,
with a closing `... ve N öğrenci daha.` for the rest. Telegram also has a hard
4096-character limit, and `send_telegram` currently **truncates** rather than
failing if the message exceeds it — so raising `SHAP_TOP_N_FEATURES` or shipping
longer `FEATURE_LABELS` can silently cut the tail off the message.

The Turkish labels come from `FEATURE_LABELS` in `config.py` — one table, replaced
per client along with `FEATURES`. The values next to each reason are joined from
today's student data; a missing one shows as `veri yok` rather than failing the
message.

| Channel | Needs |
|---|---|
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `email` | `SMTP_HOST`, `SMTP_TO`, and one of `SMTP_FROM` / `SMTP_USER`. `SMTP_PASSWORD` only when the server wants a login — Gmail needs an App Password, not your login password |
| `webhook` | `ALERT_WEBHOOK_URL` |

### The operator channel is not the customer channel

Everything above delivers the **customer's** daily message. A **failed run** goes
somewhere else entirely — `OPS_TELEGRAM_CHAT_ID` (same bot, our chat) or
`OPS_ALERT_WEBHOOK_URL`, handled by `src/notifications/ops.py` and never by
`NOTIFY_CHANNELS`. A mentor cannot act on "KeyError in daily_pipeline", and it is
exactly the kind of message that ends their confidence in the product. See
[Koşu başarısız olursa](#koşu-başarısız-olursa).

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
- the most recent run is **older than `--max-age-hours`** (default 26) — if the
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
| `GET /health` | no | `ok` only when the model, explainer, calibrator **and** meta are all loaded; otherwise `degraded` with the per-component flags. No file path, no load error |
| `GET /students?threshold=` | **no** | score everyone, return those at or above the threshold — what a dashboard should call |
| `GET /predict/{student_id}` | no | one student: probability, reasons, and the feature values behind them |
| `POST /predict` | no | score one student who is not in the daily data. Takes **engineered** features, not a raw export row — see `API_CONTRACT.md` |
| `GET /metrics` | no | an allow-listed subset of the loaded model's metadata and metrics — never the paths, the data hash, the imputation medians or the per-segment error breakdown that `model_meta.json` also holds |

Every endpoint except `/health` requires the `X-API-Key` header — including `/docs`,
`/redoc` and `/openapi.json`. Without `API_KEY` the API does not start at all; the
only exception is `EOAI_ALLOW_NO_AUTH=1` with a loopback `API_BIND_HOST`, for local
development.

A browser must not hold that key. The dashboard therefore calls its **own** origin
(`/api/...`) and the nginx that serves it proxies to this API, adding the header
server-side from a runtime environment variable — so the key is in no bundle, the
dashboard needs no cross-origin permission, and the API port does not have to be
published for it to work. See "Reaching the API from a browser" in
**`API_CONTRACT.md`**.

Nothing on this list writes. The day's run — the one operation that records a run
and therefore defines tomorrow's `new` / `still_at_risk` — is
`python -m pipeline.daily_pipeline`, run by the scheduler. It used to be
`POST /run-daily-pipeline`; that endpoint is gone.

The access log writes the route template (`/predict/{student_id}`) and a per-request
id, never the student id, and a rejected request is told what kind of thing was wrong
without the ids or column names that go to the log. What is logged and how long to
keep it: **`docs/LOGGING.md`**.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The 9 database tests skip unless you give them a throwaway database:

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

Two caveats on that last sentence, both real:

- CI has no Postgres service, so the 9 database tests **always skip there** and
  the `-q` output still shows green. Nothing in CI ever executes `db/schema.sql`.
- the fixture blanks `DATABASE_URL` but not `NOTIFY_CHANNELS` / `TELEGRAM_*` /
  `SMTP_*`. Today no test sends anything because every test passes `enabled=`
  explicitly — that is observed behaviour, not an enforced guarantee.

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
Harmless today. When Starlette drops support it will break the test suite and
step 8 of `verify_backend.py` — swapping `httpx` for `httpx2` in
`requirements.txt` is the one-line fix.

**`InconsistentVersionWarning` when unpickling the calibrator**
The calibrator was saved by a different scikit-learn version than the one you have.
It still loads. Retrain (`python running_train_pipeline.py`) to clear it.

---

## Onboarding a new client

The system is meant to be re-pointed at a new dataset rather than rewritten, and
the **model-facing** configuration really is centralised. But the honest scope is
"the feature list is one config edit", not "onboarding is one config edit". Below
is everything that actually has to change, in the order you would hit it.

### The feature list — genuinely just config

1. **`config.py`** — `RAW_DATA_PATH` / `DAILY_DATA_PATH`, then `FEATURES`,
   `CAT_COLS`, `STUDENT_INFO`, `TARGET_FEATURE`, `FEATURE_BOUNDS`,
   `CATEGORICAL_LEVELS`, `INTEGER_FEATURES`, `FLAG_FEATURES`,
   `FEATURE_LABELS`, plus `PLAN_MONTHS` (hard-codes `Aylık` / `3 Aylık` / `Yıllık`)
   and the model filename in `MODEL_PATH`.
   `_validate_feature_config()` will tell you what you missed at import time.
2. **`src/data/features.py`** — the recipe: `UNIMPUTABLE_REQUIRED`,
   `MISSING_FLAG_COLUMNS`, `IMPUTE_GROUP_COLUMN`, and `DERIVED_COLUMNS` /
   `add_monthly_value()` — which is welded to the one `monthly_fee_try` →
   `monthly_value_try` pair and will need generalising for a different derivation.
3. **`DECISION_COST`** and **`PRECISION_AT_K`** — the real relative cost of a false
   alarm versus a missed churn, and how many students a mentor can actually contact
   per run. These drive threshold selection and are a business conversation, not
   hyperparameters. Make them agree with each other: a threshold that flags ten
   times the mentor capacity is not an operating point, it is a sorted list.
4. Retrain: `python running_train_pipeline.py`.

### What else has to change, and what the old version of this section got wrong

The previous version of this page claimed "no other file needs to change" and "the
database schema does not change either". Both were wrong. The JSONB argument holds
for **features**, and only for features — the *entity* is hard-coded:

5. **`src/data/loader.py`** — every SQL statement hard-codes `student_id` and
   `enrollment_date` (the select, the upsert and its `ON CONFLICT`, the
   previous-run lookups, the alert insert), and `_coerce_types` special-cases
   `enrollment_date`. `STUDENT_INFO` drives the feature/id *split*, not the column
   names.
6. **`db/schema.sql`** — `daily_students.student_id` / `enrollment_date` are typed
   columns and `alerts.student_id` is a typed foreign key. A client whose entity is
   `learner_id` or `contract_id` needs a migration.
7. **`scripts/init_db.py`** — the `EXPECTED` dict repeats the same column names.
8. **`src/notifications/message.py`** — the entire message is Turkish literals
   (`veri yok`, `riski artırıyor` / `riski azaltıyor`, the section headings, the
   `_missing` suffix convention). `FEATURE_LABELS` covers the feature names, not
   the sentences around them. A non-Turkish client rewrites this module.
9. **`pipeline/training_pipeline.py`** — `"is_synthetic_data": True` is hard-coded.
   Leave it and every metric you ever show that client is flagged synthetic.
10. **`src/model/baseline.py`** — `single_rule_baseline` defaults to
    `days_since_last_contact`; **`scripts/compare_feature_sets.py`** —
    `CONTACT_FEATURES` is EO-specific.
11. **The dashboard repo** — `src/adapters.js` keeps its *own* copy of the Turkish
    labels (which will drift from `config.py`) and maps ~14 named fields;
    `DetailDrawer.jsx` has its own `RAW_FIELDS` list.
12. **`tests/`** — `test_loader.py`, `test_notifications.py` and
    `test_db_integration.py` all reference EO column names, and `conftest.py` loads
    `RAW_DATA_PATH`. The suite, and therefore CI, fails on day one of a new
    vertical until they are updated.

Reducing 5–7 to `STUDENT_INFO`-driven SQL is the single change that would make the
"template" claim true. It is not done yet.

---

## Known limitations

- **The model is trained on synthetic data, and that data has two informative
  columns.** Measured with `python scripts/compare_feature_sets.py`:

  | feature set | PR-AUC |
  |---|---|
  | all 24 | 0.512 |
  | without `mentor_contact_freq_per_month` + `days_since_last_contact` | 0.380 |
  | only those two, plus the categoricals (7) | 0.508 |

  Two columns carry the signal; the other twenty add 0.004. That is why every
  at-risk student's explanation is led by the same feature - it is a property of
  the dataset, not something feature selection can fix. Dropping those columns
  does not diversify the explanations, it destroys the model. Every metric, and
  the chosen threshold, is a placeholder until real client data arrives.
- **And those two columns describe mentor behaviour, which may be an effect of
  churn rather than a cause.** "Days since a mentor last contacted this student"
  partly measures disengagement that has already happened — a student who has
  mentally quit stops answering, so the mentor stops calling. If that is what the
  model is learning, it reports the past rather than warning about the future, and
  it degrades precisely when the product is used correctly: adopt a "contact
  everyone every 7 days" policy and the dominant feature flattens for the whole
  cohort, so the model returns low risk for everyone including the students who
  leave next month. Resolving this needs one fact about the data that is not
  currently written down anywhere: whether those two columns are measured strictly
  *before* the window in which churn is observed. Until that is answered, no claim
  about real-world performance should be made.
- **The shipped model loses to its own logistic-regression baseline.** PR-AUC
  0.512 against 0.537, ROC-AUC 0.718 against 0.734 — same split, both scored as
  uncalibrated rankings. `scripts/compare_feature_sets.py` states the intended
  acceptance rule ("CatBoost must beat logistic regression, otherwise the model
  has no value"); by that rule this model has not earned its place on this data.
  It costs a serving dependency and the SHAP explainer, so this needs settling on
  real data before the gradient-boosted model is assumed to be the right one.
- **The operating point is measurably more optimistic than the file suggests.**
  The validation set does three jobs — early stopping, calibrator fit and
  threshold selection — so the threshold is chosen on rows the model was tuned to
  fit. At the identical threshold 0.29: validation cost 321 and recall 0.685;
  test cost 376 (+17%) and recall 0.571. Production will look like the test
  number at best. The fix is a fourth split, or nesting the early-stopping split
  inside train.
- **The imputer is fit before the split.** `build_training_frame` learns the
  per-plan medians on the whole frame, then the data is split, so validation and
  test rows contributed to their own fill values. The effect is small at this size
  (medians over ~1100 rows per plan) and will not be on a small client pilot.
- **The split is random, not temporal.** For an *early-warning* product the
  holdout should be by date, so the reported numbers answer "does this generalise
  to next month's students" rather than "to these students' cohort-mates".
- **The threshold and the stated mentor capacity disagree.** At 0.29 the model
  flags 244 of 677 students — 36% of the book — while `PRECISION_AT_K = 20` says a
  mentor can contact twenty per run. `DECISION_COST` assumes unlimited outreach
  capacity (a false alarm costs a fixed 1 unit however many you generate). Both
  cannot be right. In practice mentors work the top of the list, which makes
  `precision@20 = 0.75` the number that describes the real workflow.
- **The scheduler misses a window it was asleep for.** The daily run now runs
  itself (the `scheduler` compose service, B-14), but it sleeps until the next
  `RUN_AT`: if the machine is off at 09:00 the run does not happen at 09:15 when it
  comes back, tomorrow's time is computed instead. The heartbeat makes that loud
  rather than silent, which is the trade this design accepts on purpose — a catch-up
  run at an arbitrary hour would redefine `new` / `still_at_risk` at that hour.
- **Authentication is all-or-nothing, and the proxy is the only client that has a
  key.** Auth is fail-closed (no `API_KEY`, no start, unless
  `EOAI_ALLOW_NO_AUTH=1` on a loopback address) and the dashboard now works with a
  key set, because its nginx injects the header server-side (D-09). What is still
  missing is anything finer: one shared secret, no per-user identity, no rotation
  and no audit of *who* looked at which student. For a pilot with named mentors
  that is the next thing to build, not a detail.
- **The no-auth demo mode trusts the whole compose network.** With
  `EOAI_ALLOW_NO_AUTH=1` the API accepts any request that reaches it, and the
  dashboard's proxy now reaches it over the container network rather than the
  loopback publish. That network is still local to the machine, so the B-07 claim
  holds — but the moment anything other than this stack joins it, set a real
  `API_KEY`.
- **No backups.** `alerts` is the only record of what the system ever did and
  nothing dumps it on a schedule. See [Backing up](#backing-up).
- **No run identity, and an empty run leaves no trace.** A run is identified only
  by a Python-side microsecond timestamp; there is no `runs` table. The scheduler's
  state file records whether the LAST run succeeded or failed, which is what the
  heartbeat and the health status read — but it is one line, not a history: there is
  still no per-run row, no duration and no record of a run that found nobody. A run that
  finds nobody at risk writes nothing at all, so "the pipeline ran and everyone is
  fine" and "the pipeline has been dead for three days" are indistinguishable —
  and the next run compares against a stale baseline. For an alerting product,
  silence must not be ambiguous.
- **No model version or threshold on alert rows, and no feature snapshot.** Which
  model and which threshold produced a given alert is not recorded, so the history
  silently changes meaning on every retrain (the threshold is cost-derived, so it
  moves on its own). And although `score_students` computes the exact feature
  values it scored, the write path drops them — so "why did you flag this student
  on 3 September" has no answer, and the outcome analysis the product's value
  claim rests on ("of those we flagged, how many actually churned") cannot be run.
- **The model file itself is unversioned.** `MODEL_PATH` is a fixed filename, so
  retraining overwrites the served model in place with no rollback.
- **`is_synthetic_data` is hard-coded `True`** in the training pipeline, so the
  disclosure flag this README leans on cannot turn itself off after a real
  training run.
- **`daily_students` is never pruned in `db` mode.** See
  [Setting up Postgres](#setting-up-postgres).
- **No KVKK/GDPR erasure path.** `alerts.student_id` is a foreign key with no
  `ON DELETE` policy and nothing deletes from `daily_students`, so an erasure
  request can only be honoured by destroying that student's alert history. There
  is no retention period and no anonymisation routine.
- **Student ids reach the logs and some error bodies.** The access log records
  `/predict/STU300001`, and validation failures return real ids and the internal
  column list in the `detail` field. Feature values and query strings are not
  logged.
- **The datasets and the `RnD/` directory ship inside the Docker image.**
  `.dockerignore` does not exclude `data/`, `RnD/` or `notebooks/`, and
  `.gitignore` commits `data/*.csv` on purpose so a clone runs immediately. That
  is fine while the data is synthetic; the day a real client export lands, every
  image handed to anyone carries their student records.
- **`RnD/` is not part of the product.** It is excluded from CI, imported by
  nothing, `model_2.py` does not currently import at all, and `model_3.py`
  re-applies feature engineering to an already-engineered frame — so the
  ANN-versus-CatBoost numbers committed under `metrics/` were computed on
  different features and should not be quoted.
- **No endpoint reads the `alerts` table.** History and `status` are recorded but
  not exposed over the API, so a dashboard cannot show them yet.
- **`GET /students` re-scores everything on every call**, with no pagination and
  no limit. Fine at this size (~70 ms for 25 students), slow at ten thousand, and
  a single `?threshold=0` on a large roster can occupy the worker pool long enough
  for the container healthcheck to fail.
- **Not deployed anywhere, and nothing in the repo terminates TLS, rate-limits or
  caps request size.** Those are a reverse-proxy decision that has not been made.
