"""Round-trip tests for the Postgres backend.

These need a real database, so they are skipped unless `TEST_DATABASE_URL` is set:

    createdb eo_churn_test
    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/eo_churn_test pytest

It must be a THROWAWAY database - every test truncates both tables. The variable is
deliberately not `DATABASE_URL`, so running `pytest` in a shell configured for
development can never write to the database you actually use.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from config import DAILY_DATA_PATH
from src.data import loader

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (REPO_ROOT / "db" / "schema.sql").read_text()


@pytest.fixture
def db(monkeypatch, isolate_backend):
    """A clean database with the repo's schema applied, wired into `loader`.

    Depends on `isolate_backend` (conftest, autouse) so that this fixture's
    patches are applied after it and win: these tests DO want a database.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine(url)

    # Point the module's lazy engine at the test database for the duration.
    monkeypatch.setattr(loader, "DATABASE_URL", url)
    monkeypatch.setattr(loader, "_engine", None)

    with engine.begin() as connection:
        connection.exec_driver_sql(SCHEMA_SQL)
        connection.exec_driver_sql("TRUNCATE alerts, daily_students RESTART IDENTITY CASCADE")

    yield engine

    loader._engine = None
    engine.dispose()


@pytest.fixture
def daily_csv() -> pd.DataFrame:
    return pd.read_csv(DAILY_DATA_PATH)


def test_schema_sql_can_be_applied_twice(db):
    """`psql -f db/schema.sql` on an existing database must be a no-op, not an error."""
    with db.begin() as connection:
        connection.exec_driver_sql(SCHEMA_SQL)


def test_csv_survives_the_round_trip(db, daily_csv):
    """Load the CSV, read it back: same values, same dtypes.

    This is the contract the whole dual-mode design rests on - if the DB frame and
    the CSV frame differ, the two backends silently score students differently.
    """
    loader.upsert_daily_students_db(daily_csv)
    from_db = loader.load_daily_students_db()

    assert set(from_db.columns) == set(daily_csv.columns)
    # JSONB does not preserve key order, so column ORDER is not part of the contract
    # (every consumer selects config.FEATURES explicitly). Align, then compare.
    from_db = from_db[daily_csv.columns].sort_values("student_id").reset_index(drop=True)
    expected = daily_csv.sort_values("student_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(from_db, expected)


def test_nulls_survive_as_nulls(db, daily_csv):
    """A missing feature must come back as NaN, not as the string "NaN" or a zero."""
    assert daily_csv["satisfaction_survey_score"].isna().any(), "fixture no longer has nulls"

    loader.upsert_daily_students_db(daily_csv)
    from_db = loader.load_daily_students_db().set_index("student_id")
    expected = daily_csv.set_index("student_id")

    null_ids = expected.index[expected["satisfaction_survey_score"].isna()]
    assert from_db.loc[null_ids, "satisfaction_survey_score"].isna().all()


def test_upsert_is_idempotent(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv)
    first = loader.load_daily_students_db()

    loader.upsert_daily_students_db(daily_csv)
    second = loader.load_daily_students_db()

    assert loader.count_daily_students_db() == len(daily_csv)
    pd.testing.assert_frame_equal(
        first.sort_values("student_id").reset_index(drop=True),
        second.sort_values("student_id").reset_index(drop=True),
    )


def test_upsert_updates_an_existing_student(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv)

    changed = daily_csv.copy()
    changed.loc[0, "days_since_last_contact"] = 999.0
    loader.upsert_daily_students_db(changed)

    student_id = changed.loc[0, "student_id"]
    from_db = loader.load_daily_students_db().set_index("student_id")
    assert from_db.loc[student_id, "days_since_last_contact"] == 999.0
    assert loader.count_daily_students_db() == len(daily_csv)  # updated, not duplicated


def test_empty_frame_writes_nothing(db):
    assert loader.upsert_daily_students_db(pd.DataFrame()) == 0
    assert loader.count_daily_students_db() == 0


# --- history: one snapshot per student per as_of_date -------------------------
DAY_1 = date(2026, 9, 1)
DAY_2 = date(2026, 9, 8)


def _row_count(engine) -> int:
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT count(*) FROM daily_students").scalar()


def test_two_as_of_dates_keep_both_snapshots(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv, DAY_1)
    loader.upsert_daily_students_db(daily_csv, DAY_2)

    assert _row_count(db) == 2 * len(daily_csv)
    assert loader.count_daily_students_db() == len(daily_csv)  # distinct students
    assert loader.count_daily_students_db(DAY_1) == len(daily_csv)
    assert loader.count_daily_students_db(DAY_2) == len(daily_csv)


def test_loading_the_same_date_twice_does_not_add_rows(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv, DAY_1)
    loader.upsert_daily_students_db(daily_csv, DAY_1)
    assert _row_count(db) == len(daily_csv)


def test_scoring_reads_only_the_latest_snapshot(db, daily_csv):
    older = daily_csv.copy()
    older.loc[0, "days_since_last_contact"] = 111.0
    newer = daily_csv.copy()
    newer.loc[0, "days_since_last_contact"] = 222.0

    # Newer date written FIRST: "latest" must mean the highest as_of_date, not the
    # most recent write.
    loader.upsert_daily_students_db(newer, DAY_2)
    loader.upsert_daily_students_db(older, DAY_1)

    from_db = loader.load_daily_students_db().set_index("student_id")
    assert len(from_db) == len(daily_csv)
    assert from_db.loc[daily_csv.loc[0, "student_id"], "days_since_last_contact"] == 222.0
    assert loader.latest_as_of_date_db() == DAY_2


def test_latest_as_of_date_is_none_for_an_empty_table(db):
    assert loader.latest_as_of_date_db() is None


def test_history_window_returns_only_snapshots_in_range(db, daily_csv):
    for day in (date(2026, 8, 1), date(2026, 8, 25), date(2026, 9, 1)):
        loader.upsert_daily_students_db(daily_csv, day)

    history = loader.load_student_history_db(date(2026, 9, 1), days=10)

    assert set(history["as_of_date"].dt.date) == {date(2026, 8, 25), date(2026, 9, 1)}
    assert len(history) == 2 * len(daily_csv)


def test_trend_from_two_stored_snapshots(db, daily_csv):
    """The acceptance case: two loads a week apart give a 7-day delta, and no 28-day one."""
    from src.data.features import add_trend_features

    first = daily_csv.copy()
    second = daily_csv.copy()
    second["program_adherence_rate"] = first["program_adherence_rate"] + 0.1
    loader.upsert_daily_students_db(first, DAY_1)
    loader.upsert_daily_students_db(second, DAY_2)

    current = loader.load_daily_students_db()
    history = loader.load_student_history_db(DAY_2, days=31)
    out = add_trend_features(current, history, DAY_2, {"program_adherence_rate": [7, 28]})

    assert ((out["program_adherence_rate_delta_7d"] - 0.1).abs() < 1e-9).all()
    assert (out["program_adherence_rate_delta_7d_missing"] == 0).all()
    assert out["program_adherence_rate_delta_28d"].isna().all()
    assert (out["program_adherence_rate_delta_28d_missing"] == 1).all()


# --- the alert log ----------------------------------------------------------
def _alerts(student_ids, status="new"):
    return pd.DataFrame(
        {
            "student_id": list(student_ids),
            "churn_probability": [0.5] * len(student_ids),
            "status": [status] * len(student_ids),
            "top_reasons": ["days_since_last_contact (+0.5)"] * len(student_ids),
            "top_reasons_detail": [
                [{"feature": "days_since_last_contact", "impact": 0.5}]
            ]
            * len(student_ids),
        }
    )


def test_previous_at_risk_ids_reads_only_the_last_run(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv)
    ids = list(daily_csv["student_id"][:3])
    older = datetime.now(timezone.utc) - timedelta(days=1)

    loader.append_to_alert_log_db(_alerts(ids[:2]), run_at=older)
    loader.append_to_alert_log_db(_alerts(ids[2:]), run_at=datetime.now(timezone.utc))

    # Only the most recent run counts - a student flagged yesterday and quiet today
    # is `new` again, which is what the CSV backend does too.
    assert loader.previous_at_risk_ids_db() == set(ids[2:])


def test_alert_rows_keep_their_structured_explanation(db, daily_csv):
    loader.upsert_daily_students_db(daily_csv)
    student_id = daily_csv["student_id"].iloc[0]
    loader.append_to_alert_log_db(_alerts([student_id]))

    latest = loader.latest_run_alerts_db()
    assert len(latest) == 1
    assert latest["top_reasons_detail"].iloc[0] == [
        {"feature": "days_since_last_contact", "impact": 0.5}
    ]


def test_one_run_gets_one_timestamp(db, daily_csv):
    """`previous_at_risk_ids_db` groups a run by its exact run_at - so every row of
    one run must share one value, not one `now()` per row."""
    loader.upsert_daily_students_db(daily_csv)
    loader.append_to_alert_log_db(_alerts(daily_csv["student_id"][:5]))

    assert loader.latest_run_alerts_db()["run_at"].nunique() == 1
    assert len(loader.latest_run_alerts_db()) == 5
