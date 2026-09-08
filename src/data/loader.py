"""Data access for the churn system.

Two independent concerns live here, and they deliberately do not share a code path:

1. **Training data — always CSV.** `data_loader()` is what
   `pipeline/training_pipeline.py` and `scripts/build_training_data.py` read with.
   Training is a batch job over a versioned file; it is not routed through the
   database.

2. **Daily serving data and the alert log — dual-mode.** Every operation comes as
   a `_csv` / `_db` pair with a dispatcher on top that picks one based on
   `config.DATA_SOURCE` (`"csv"` or `"db"`). Flip `DATA_SOURCE` in `.env` and the
   daily pipeline changes backend without a code change; if one backend breaks,
   switch back to the other.

The dispatchers are the only functions the pipeline and the API should call.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import (
    CAT_COLS,
    DAILY_ALERTS_PATH,
    DAILY_DATA_PATH,
    DATA_SOURCE,
    DATABASE_URL,
    STUDENT_INFO,
)
from src.serialization import to_native

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]

# Columns written to the alert log, in this order. The CSV file additionally
# carries `run_date` (kept for scripts/send_daily_alerts.py); the database keeps
# the structured explanation in `top_reasons_detail` (JSONB), which the CSV
# cannot hold.
_ALERT_COLUMNS = [_ID_COLUMN, "churn_probability", "status", "top_reasons"]


# --- Engine ---------------------------------------------------------------
_engine = None


def _get_engine():
    """Create the SQLAlchemy engine once, lazily.

    Lazily, so that importing this module in CSV mode never needs a database, and
    the CSV path keeps working on a machine that has no Postgres at all.
    """
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set, so there is no database to talk to. "
                "Set it in .env (see .env.example) - it is required by DATA_SOURCE=db "
                "and by scripts/load_daily_students.py. To stay on files instead, "
                "set DATA_SOURCE=csv."
            )
        from sqlalchemy import create_engine  # imported here: CSV mode needs no driver

        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def _sql(query: str):
    from sqlalchemy import text

    return text(query)


def _resolve(source: str | None) -> str:
    """`source` overrides config.DATA_SOURCE - used by tests and by callers that
    want to force one backend for a single call."""
    resolved = (source or DATA_SOURCE or "csv").lower()
    if resolved not in {"csv", "db"}:
        raise ValueError(f"DATA_SOURCE must be 'csv' or 'db', got {resolved!r}")
    return resolved


# --- Training data (CSV only) ------------------------------------------------
def data_loader(path):
    """Read a CSV into a DataFrame. The training pipeline's only loader."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Data file not found: {path}")
    return pd.read_csv(path)


# Explicit alias, so a caller reading the dual-mode section below sees that this
# one is the CSV implementation and has no `_db` counterpart on purpose.
data_loader_csv = data_loader


# --- Daily serving data (dual-mode) -----------------------------------------
def load_daily_students_csv(path=DAILY_DATA_PATH) -> pd.DataFrame:
    """Today's students from `data/daily_data.csv`."""
    return data_loader(path)


def load_daily_students_db() -> pd.DataFrame:
    """Today's students from the `daily_students` table.

    The table stores the id columns natively and every feature column packed into
    a `features` JSONB blob; this unpacks it back into the same flat frame the CSV
    path returns, so everything downstream is backend-agnostic.
    """
    df = pd.read_sql(
        _sql("SELECT student_id, enrollment_date, features FROM daily_students"),
        _get_engine(),
    )
    if df.empty:
        return df.drop(columns=["features"])

    # psycopg2 decodes JSONB to a dict; a text column (or a driver that does not)
    # would hand us a string instead.
    features = df["features"].apply(lambda v: json.loads(v) if isinstance(v, str) else v)
    features_df = pd.json_normalize(features)

    flat = pd.concat(
        [df[list(STUDENT_INFO)].reset_index(drop=True), features_df.reset_index(drop=True)],
        axis=1,
    )
    return _coerce_types(flat)


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Make the DB frame match the dtypes `pd.read_csv` would have produced.

    JSONB round-trips can hand back numbers as strings (depending on how the rows
    were loaded), which would silently turn a numeric feature into a categorical
    one. Nulls stay null - feature engineering imputes them later.
    """
    df = df.copy()
    for column in df.columns:
        if column in CAT_COLS or column in STUDENT_INFO:
            continue
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "enrollment_date" in df.columns:
        df["enrollment_date"] = df["enrollment_date"].astype(str)
    return df


def load_daily_students(path=DAILY_DATA_PATH, *, source: str | None = None) -> pd.DataFrame:
    """Today's students from whichever backend `DATA_SOURCE` selects."""
    if _resolve(source) == "db":
        return load_daily_students_db()
    return load_daily_students_csv(path)


# --- Daily serving data: writing (DB only) ----------------------------------
# There is no `_csv` counterpart on purpose. Writing the CSV means editing the file
# the client hands you, which is their job, not the pipeline's; the database is the
# only backend this system actually loads data INTO.
def upsert_daily_students_db(df: pd.DataFrame) -> int:
    """Insert or update today's students in `daily_students`. Returns rows written.

    Existing students are updated in place (matched on `student_id`), so this is
    safe to re-run: loading the same file twice leaves the table identical, not
    doubled. Students present in the table but absent from `df` are left alone -
    `alerts` references them, so removing one would break its history.

    Every column except `config.STUDENT_INFO` is packed into the `features` JSONB
    blob, which is exactly what `load_daily_students_db()` unpacks on the way out.
    """
    if df.empty:
        logger.info("daily_students: nothing to write")
        return 0

    feature_columns = [c for c in df.columns if c not in STUDENT_INFO]
    records = []
    for _, row in df.iterrows():
        features = to_native(row[feature_columns].to_dict())
        records.append(
            {
                "student_id": str(row[_ID_COLUMN]),
                "enrollment_date": to_native(row.get("enrollment_date")),
                # allow_nan=False: a NaN that slipped through must raise here rather
                # than become the literal `NaN`, which is not valid JSON and which
                # Postgres would reject (or worse, store as a string).
                "features": json.dumps(features, allow_nan=False),
            }
        )

    with _get_engine().begin() as connection:
        connection.execute(
            _sql(
                "INSERT INTO daily_students (student_id, enrollment_date, features) "
                "VALUES (:student_id, :enrollment_date, :features) "
                "ON CONFLICT (student_id) DO UPDATE SET "
                "  enrollment_date = EXCLUDED.enrollment_date, "
                "  features        = EXCLUDED.features, "
                "  updated_at      = now()"
            ),
            records,
        )
    logger.info("daily_students: %d row(s) written", len(records))
    return len(records)


def count_daily_students_db() -> int:
    """How many students the table currently holds."""
    with _get_engine().connect() as connection:
        return int(connection.execute(_sql("SELECT count(*) FROM daily_students")).scalar())


# --- Alert-log reads (dual-mode) --------------------------------------------
# "Was this student already at risk in the PREVIOUS run?" - which is what decides
# `new` vs `still_at_risk`. Note this is deliberately the previous *run*, not the
# student's last-ever status: a student flagged three runs ago and quiet since is
# `new` again, in both backends.
def previous_at_risk_ids_csv(alerts_path=DAILY_ALERTS_PATH) -> set[str]:
    path = Path(alerts_path)
    if not path.exists():
        return set()
    log = pd.read_csv(path)
    if log.empty:
        return set()
    last_run = log["run_at"].max()
    return set(log.loc[log["run_at"] == last_run, _ID_COLUMN].astype(str))


def previous_at_risk_ids_db() -> set[str]:
    df = pd.read_sql(
        _sql(
            "SELECT student_id FROM alerts "
            "WHERE run_at = (SELECT max(run_at) FROM alerts)"
        ),
        _get_engine(),
    )
    return set(df[_ID_COLUMN].astype(str))


def previous_at_risk_ids(alerts_path=DAILY_ALERTS_PATH, *, source: str | None = None) -> set[str]:
    if _resolve(source) == "db":
        return previous_at_risk_ids_db()
    return previous_at_risk_ids_csv(alerts_path)


# --- Alert-log writes (dual-mode) -------------------------------------------
def append_to_alert_log_csv(at_risk: pd.DataFrame, alerts_path=DAILY_ALERTS_PATH, *, run_at=None) -> None:
    """Append this run's at-risk students to the alert CSV."""
    run_at = run_at or datetime.now(timezone.utc)
    rows = at_risk[_ALERT_COLUMNS].copy()
    rows.insert(0, "run_at", run_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    rows.insert(1, "run_date", run_at.date().isoformat())

    path = Path(alerts_path)
    rows.to_csv(path, mode="a", header=not path.exists(), index=False)
    logger.info("daily run: %d at-risk students appended to %s", len(rows), alerts_path)


def append_to_alert_log_db(at_risk: pd.DataFrame, *, run_at=None) -> None:
    """Append this run's at-risk students to the `alerts` table.

    `run_at` is passed explicitly rather than left to the column default, so every
    row of one run shares one timestamp - `previous_at_risk_ids_db()` groups runs
    by that exact value.
    """
    run_at = run_at or datetime.now(timezone.utc)
    if at_risk.empty:
        logger.info("daily run: no at-risk students, nothing written to alerts")
        return

    records = [
        {
            "run_at": run_at,
            "student_id": str(row[_ID_COLUMN]),
            "churn_probability": float(row["churn_probability"]),
            "status": row["status"],
            "top_reasons": row["top_reasons"],
            # jsonb columns take a JSON string; psycopg2 cannot adapt a list of dicts.
            "top_reasons_detail": json.dumps(row.get("top_reasons_detail") or []),
        }
        for _, row in at_risk.iterrows()
    ]

    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(
            _sql(
                "INSERT INTO alerts "
                "(run_at, student_id, churn_probability, status, top_reasons, top_reasons_detail) "
                "VALUES (:run_at, :student_id, :churn_probability, :status, :top_reasons, "
                ":top_reasons_detail)"
            ),
            records,
        )
    logger.info("daily run: %d at-risk students inserted into alerts", len(records))


def append_to_alert_log(
    at_risk: pd.DataFrame, alerts_path=DAILY_ALERTS_PATH, *, run_at=None, source: str | None = None
) -> None:
    if _resolve(source) == "db":
        append_to_alert_log_db(at_risk, run_at=run_at)
        return
    append_to_alert_log_csv(at_risk, alerts_path, run_at=run_at)


# --- Alert-log reads: the whole latest run (dual-mode) -----------------------
# Used by scripts/send_daily_alerts.py, which reports the run that just happened.
def latest_run_alerts_csv(alerts_path=DAILY_ALERTS_PATH) -> pd.DataFrame:
    path = Path(alerts_path)
    if not path.exists():
        return pd.DataFrame()
    log = pd.read_csv(path)
    if log.empty:
        return log
    return log[log["run_at"] == log["run_at"].max()]


def latest_run_alerts_db() -> pd.DataFrame:
    return pd.read_sql(
        _sql(
            "SELECT run_at, student_id, churn_probability, status, top_reasons, "
            "top_reasons_detail FROM alerts "
            "WHERE run_at = (SELECT max(run_at) FROM alerts) "
            "ORDER BY churn_probability DESC"
        ),
        _get_engine(),
    )


def latest_run_alerts(alerts_path=DAILY_ALERTS_PATH, *, source: str | None = None) -> pd.DataFrame:
    """Every row of the most recent recorded run, from whichever backend is active."""
    if _resolve(source) == "db":
        return latest_run_alerts_db()
    return latest_run_alerts_csv(alerts_path)
