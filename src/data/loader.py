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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    CAT_COLS,
    DAILY_ALERTS_PATH,
    DAILY_DATA_PATH,
    DATA_SOURCE,
    DATABASE_URL,
    SCHEDULER_TIMEZONE,
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


# flock is POSIX-only. Linux and macOS are the platforms this runs on; anywhere
# else the append keeps working, it just loses the guarantee, so this must not be
# an import error.
try:
    import fcntl
except ImportError:  # pragma: no cover - not reachable on Linux/macOS
    fcntl = None


def _lock_exclusive(file_handle) -> None:
    if fcntl is not None:
        fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)


def _unlock(file_handle) -> None:
    if fcntl is not None:
        fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)


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
        _sql("SELECT student_id, enrollment_date, features FROM daily_students "
            "WHERE as_of_date = (SELECT max(as_of_date) FROM daily_students)"), # Scoring uses the latest as_of_date
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
        if column in CAT_COLS or column in STUDENT_INFO or column == "as_of_date":
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


def history_available(source: str | None = None) -> bool:
    """True when the active backend keeps snapshot history (DB only)."""
    return _resolve(source) == "db"


def load_student_history_db(as_of: date, days: int) -> pd.DataFrame:
    """
    Snapshots in [as_of - days, as_of], flat like load_daily_students_db plus as_of_date.
    Returns a flat DataFrame (one row per student per as_of_date) with
    student_id, as_of_date, and every feature column expanded from the
    JSONB `features` field — the same shape as load_daily_students_db,
    plus the as_of_date column so callers can distinguish snapshots.
    """
    df = pd.read_sql(
        _sql(
            "SELECT student_id, as_of_date, features FROM daily_students "
            "WHERE as_of_date BETWEEN :start AND :end"
        ),
        _get_engine(),
        params={"start": as_of - timedelta(days=days), "end": as_of},
    )
    if df.empty:
        return df.drop(columns=["features"])
    features = df["features"].apply(lambda v: json.loads(v) if isinstance(v, str) else v)
    flat = pd.concat(
        [df[["student_id", "as_of_date"]].reset_index(drop=True),
         pd.json_normalize(features).reset_index(drop=True)],
        axis=1,
    )
    flat = _coerce_types(flat)
    flat["as_of_date"] = pd.to_datetime(flat["as_of_date"])
    return flat


# --- Daily serving data: writing (DB only) ----------------------------------
# There is no `_csv` counterpart on purpose. Writing the CSV means editing the file
# the client hands you, which is their job, not the pipeline's; the database is the
# only backend this system actually loads data INTO.
def upsert_daily_students_db(df: pd.DataFrame, as_of_date: date | None = None) -> int:
    """Insert or update one snapshot of students in `daily_students`. Returns rows written.

    Each load is stored under `as_of_date` (default: today), so earlier snapshots
    are kept as history. A row is matched on `(student_id, as_of_date)`: loading
    the same file for the same date twice updates in place and leaves the table
    identical, not doubled, while a different date adds new rows. Students present
    in the table but absent from `df` are left alone, and so are all other dates.

    Every column except `config.STUDENT_INFO` is packed into the `features` JSONB
    blob, which is exactly what `load_daily_students_db()` unpacks on the way out.
    """
    as_of_date = as_of_date or date.today()
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
                "as_of_date": as_of_date,
            }
        )

    with _get_engine().begin() as connection:
        connection.execute(
            _sql(
                "INSERT INTO daily_students (student_id, as_of_date, enrollment_date, features) "
                "VALUES (:student_id, :as_of_date, :enrollment_date, :features) "
                "ON CONFLICT (student_id, as_of_date) DO UPDATE SET "
                "  enrollment_date = EXCLUDED.enrollment_date, "
                "  features        = EXCLUDED.features, "
                "  updated_at      = now()"
            ),
            records,
        )
    logger.info("daily_students: %d row(s) written", len(records))
    return len(records)


def count_daily_students_db(as_of_date: date | None = None) -> int:
    """Rows in the `as_of_date` snapshot, or distinct students across all snapshots when omitted."""
    if as_of_date is None:
        query, params = "SELECT count(DISTINCT student_id) FROM daily_students", {}
    else:
        query = "SELECT count(*) FROM daily_students WHERE as_of_date = :as_of_date"
        params = {"as_of_date": as_of_date}
    with _get_engine().connect() as connection:
        return int(connection.execute(_sql(query), params).scalar())


def latest_as_of_date_db() -> date | None:
    """The most recent snapshot date in `daily_students` (None when the table is empty)."""
    with _get_engine().connect() as connection:
        return connection.execute(_sql("SELECT max(as_of_date) FROM daily_students")).scalar()

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


# The last run that completed. A `no_alerts` run counts: it IS the previous run,
# and nobody was on it. Looking only at `alerts` skipped it, compared today against
# a stale list and called a student flagged three days ago `still_at_risk`.
_LAST_COMPLETED_RUN = (
    "SELECT run_id FROM runs WHERE status IN ('ok', 'no_alerts') "
    "ORDER BY started_at DESC, run_id DESC LIMIT 1"
)


def _previous_at_risk_ids(connection) -> set[str]:
    rows = connection.execute(
        _sql(f"SELECT student_id FROM alerts WHERE run_id = ({_LAST_COMPLETED_RUN})")
    )
    return {str(row[0]) for row in rows}


def previous_at_risk_ids_db() -> set[str]:
    with _get_engine().connect() as connection:
        return _previous_at_risk_ids(connection)


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
    # Two overlapping runs (a scheduler retry catching up with the attempt it was
    # meant to replace) both append here, and pandas writes a frame in several
    # write() calls with nothing serialising them - the result is two runs spliced
    # together mid-line, which makes `previous_at_risk_ids` read garbage. One
    # exclusive lock per append makes the whole block atomic, and the header
    # decision is taken inside the lock for the same reason.
    with open(path, "a", encoding="utf-8", newline="") as f:
        _lock_exclusive(f)
        try:
            rows.to_csv(f, header=os.fstat(f.fileno()).st_size == 0, index=False)
        finally:
            f.flush()
            _unlock(f)
    logger.info("daily run: %d at-risk students appended to %s", len(rows), alerts_path)


# --- Runs (B-04) --------------------------------------------------------------
# A run is a `runs` row, written even when nobody is at risk and when the run
# fails, so "ran, nobody at risk" and "has not run for three days" are different
# facts. Only the database backend has one; see db/schema.sql.
class RunAlreadyRecordedError(RuntimeError):
    """A completed run already exists for this run_date - one per day."""


# Any fixed number works; it only has to be the same in every pipeline process,
# and differ from scripts/init_db.py's ADVISORY_LOCK_KEY.
_RUN_LOCK_KEY = 461_600_04


def run_date_of(started_at: datetime) -> date:
    """The day a run belongs to: its start, in the zone the schedule is written in."""
    return started_at.astimezone(ZoneInfo(SCHEDULER_TIMEZONE)).date()


def mark_new_or_repeat(at_risk: pd.DataFrame, previous: set[str]) -> pd.DataFrame:
    """Add a `status` column: 'new' or 'still_at_risk' vs. the previous run's ids."""
    at_risk = at_risk.copy()
    at_risk["status"] = [
        "still_at_risk" if str(student_id) in previous else "new"
        for student_id in at_risk[_ID_COLUMN]
    ]
    return at_risk


def _write_run(
    connection,
    marked: pd.DataFrame,
    *,
    started_at: datetime,
    model_version: str | None = None,
    threshold: float | None = None,
    entity_count: int | None = None,
) -> int:
    """Insert one `runs` row and its alerts inside the caller's transaction."""
    from sqlalchemy.exc import IntegrityError

    run_date = run_date_of(started_at)
    try:
        run_id = connection.execute(
            _sql(
                "INSERT INTO runs (run_date, started_at, model_version, threshold, "
                "entity_count, at_risk_count, status) "
                "VALUES (:run_date, :started_at, :model_version, :threshold, "
                ":entity_count, :at_risk_count, :status) RETURNING run_id"
            ),
            {
                "run_date": run_date,
                "started_at": started_at,
                "model_version": model_version,
                "threshold": threshold,
                "entity_count": entity_count,
                "at_risk_count": len(marked),
                "status": "ok" if len(marked) else "no_alerts",
            },
        ).scalar_one()
    except IntegrityError as e:
        if "runs_run_date_uidx" not in str(e.orig):
            raise
        raise RunAlreadyRecordedError(
            f"a run for {run_date} is already recorded - one run per day"
        ) from None

    records = [
        {
            "run_id": run_id,
            # Every row of one run shares the run's start, not one now() per row.
            "run_at": started_at,
            "student_id": str(row[_ID_COLUMN]),
            "churn_probability": float(row["churn_probability"]),
            "status": row["status"],
            "top_reasons": row["top_reasons"],
            # jsonb columns take a JSON string; psycopg2 cannot adapt a list of dicts.
            "top_reasons_detail": json.dumps(row.get("top_reasons_detail") or []),
        }
        for _, row in marked.iterrows()
    ]
    if records:
        connection.execute(
            _sql(
                "INSERT INTO alerts (run_id, run_at, student_id, churn_probability, "
                "status, top_reasons, top_reasons_detail) "
                "VALUES (:run_id, :run_at, :student_id, :churn_probability, :status, "
                ":top_reasons, :top_reasons_detail)"
            ),
            records,
        )
    return run_id


def record_run_db(
    at_risk: pd.DataFrame,
    *,
    started_at: datetime,
    model_version: str | None = None,
    threshold: float | None = None,
    entity_count: int | None = None,
) -> pd.DataFrame:
    """Mark `at_risk` against the previous run and record this run. Returns the marked frame.

    One transaction under an advisory lock, so reading the previous run and writing
    this one cannot interleave with another run: a concurrent run waits, then marks
    against this one - or, on the same run_date, is refused with
    `RunAlreadyRecordedError` by `runs_run_date_uidx`.
    """
    with _get_engine().begin() as connection:
        connection.execute(_sql(f"SELECT pg_advisory_xact_lock({_RUN_LOCK_KEY})"))
        marked = mark_new_or_repeat(at_risk, _previous_at_risk_ids(connection))
        run_id = _write_run(
            connection,
            marked,
            started_at=started_at,
            model_version=model_version,
            threshold=threshold,
            entity_count=entity_count,
        )
    logger.info("daily run %s: %d at-risk students recorded", run_id, len(marked))
    return marked


def append_to_alert_log_db(at_risk: pd.DataFrame, *, run_at=None) -> None:
    """Record an already-marked frame as one run started at `run_at` (default: now).

    For callers that set `status` themselves (scripts/seed_demo_history.py). The
    daily run uses `record_run_db`, which marks under the lock it writes under.
    """
    run_at = run_at or datetime.now(timezone.utc)
    with _get_engine().begin() as connection:
        connection.execute(_sql(f"SELECT pg_advisory_xact_lock({_RUN_LOCK_KEY})"))
        _write_run(connection, at_risk, started_at=run_at)
    logger.info("daily run: %d at-risk students inserted into alerts", len(at_risk))


def record_failed_run_db(
    *, started_at: datetime, model_version: str | None = None, threshold: float | None = None
) -> None:
    """Write a `failed` runs row: a broken day is a row, not a gap."""
    with _get_engine().begin() as connection:
        connection.execute(
            _sql(
                "INSERT INTO runs (run_date, started_at, model_version, threshold, status) "
                "VALUES (:run_date, :started_at, :model_version, :threshold, 'failed')"
            ),
            {
                "run_date": run_date_of(started_at),
                "started_at": started_at,
                "model_version": model_version,
                "threshold": threshold,
            },
        )


def latest_run_db() -> dict | None:
    """The most recent run of any outcome, `failed` included, or None if there is none."""
    with _get_engine().connect() as connection:
        row = connection.execute(
            _sql(
                "SELECT run_id, run_date, started_at, finished_at, status, at_risk_count "
                "FROM runs WHERE status <> 'superseded' "
                "ORDER BY started_at DESC, run_id DESC LIMIT 1"
            )
        ).mappings().first()
    return dict(row) if row else None


def latest_run_csv(alerts_path=DAILY_ALERTS_PATH) -> dict | None:
    """The CSV log only holds runs that flagged someone, so this is the newest of those."""
    latest = latest_run_alerts_csv(alerts_path)
    if latest.empty:
        return None
    started_at = pd.to_datetime(latest["run_at"], format="mixed", utc=True).max()
    return {"started_at": started_at.to_pydatetime(), "status": "ok"}


def latest_run(alerts_path=DAILY_ALERTS_PATH, *, source: str | None = None) -> dict | None:
    """{started_at, status, ...} of the most recent run, or None if nothing ever ran."""
    if _resolve(source) == "db":
        return latest_run_db()
    return latest_run_csv(alerts_path)


def record_run(
    at_risk: pd.DataFrame,
    alerts_path=DAILY_ALERTS_PATH,
    *,
    started_at: datetime,
    model_version: str | None = None,
    threshold: float | None = None,
    entity_count: int | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    """Mark `at_risk` new / still_at_risk and record the run. Returns the marked frame.

    The CSV backend has no `runs`: it appends the alert rows only, so there a run
    with nobody at risk still leaves no trace.
    """
    if _resolve(source) == "db":
        return record_run_db(
            at_risk,
            started_at=started_at,
            model_version=model_version,
            threshold=threshold,
            entity_count=entity_count,
        )
    marked = mark_new_or_repeat(at_risk, previous_at_risk_ids_csv(alerts_path))
    append_to_alert_log_csv(marked, alerts_path, run_at=started_at)
    return marked


def record_failed_run(
    *,
    started_at: datetime,
    model_version: str | None = None,
    threshold: float | None = None,
    source: str | None = None,
) -> None:
    """DB only; a no-op on the CSV backend, which has nowhere to put it."""
    if _resolve(source) == "db":
        record_failed_run_db(
            started_at=started_at, model_version=model_version, threshold=threshold
        )


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
    """Empty when the last completed run was `no_alerts` - never an older run's list."""
    return pd.read_sql(
        _sql(
            "SELECT run_at, student_id, churn_probability, status, top_reasons, "
            "top_reasons_detail FROM alerts "
            f"WHERE run_id = ({_LAST_COMPLETED_RUN}) "
            "ORDER BY churn_probability DESC"
        ),
        _get_engine(),
    )


def latest_run_alerts(alerts_path=DAILY_ALERTS_PATH, *, source: str | None = None) -> pd.DataFrame:
    """Every row of the most recent recorded run, from whichever backend is active."""
    if _resolve(source) == "db":
        return latest_run_alerts_db()
    return latest_run_alerts_csv(alerts_path)


# --- Alert-log reads: the run BEFORE the latest one (dual-mode) --------------
# Used by the alert message to say which way a repeat student is moving. A
# student who has been on the list for a week is far more urgent at 65% and
# rising than at 27% and flat, and the id alone does not say which.
def previous_run_probabilities_csv(alerts_path=DAILY_ALERTS_PATH) -> dict[str, float]:
    """{student_id: churn_probability} for the run before the most recent one."""
    path = Path(alerts_path)
    if not path.exists():
        return {}
    log = pd.read_csv(path)
    if log.empty or "run_at" not in log.columns:
        return {}
    runs = sorted(log["run_at"].unique())
    if len(runs) < 2:
        return {}
    previous = log[log["run_at"] == runs[-2]]
    return {
        str(row[_ID_COLUMN]): float(row["churn_probability"])
        for _, row in previous.iterrows()
    }


def previous_run_probabilities_db() -> dict[str, float]:
    df = pd.read_sql(
        _sql(
            "SELECT student_id, churn_probability FROM alerts "
            "WHERE run_id = (SELECT run_id FROM runs WHERE status IN ('ok', 'no_alerts') "
            "                ORDER BY started_at DESC, run_id DESC OFFSET 1 LIMIT 1)"
        ),
        _get_engine(),
    )
    return {
        str(row[_ID_COLUMN]): float(row["churn_probability"]) for _, row in df.iterrows()
    }


def previous_run_probabilities(
    alerts_path=DAILY_ALERTS_PATH, *, source: str | None = None
) -> dict[str, float]:
    """Empty when there is no earlier run - the message then omits the trend."""
    if _resolve(source) == "db":
        return previous_run_probabilities_db()
    return previous_run_probabilities_csv(alerts_path)
