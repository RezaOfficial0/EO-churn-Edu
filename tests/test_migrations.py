"""B-16: migrations are tracked and run once; db/schema.sql and the migrations
describe the same database.

The naming tests need nothing. The rest need a THROWAWAY Postgres, like
tests/test_db_integration.py, and are skipped without TEST_DATABASE_URL. They drop
the app tables, so never point that variable at a database you care about.
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = (REPO_ROOT / "db" / "schema.sql").read_text()

_spec = importlib.util.spec_from_file_location("init_db", REPO_ROOT / "scripts" / "init_db.py")
init_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(init_db)


# --------------------------------------------------------------------- naming

def test_repo_migrations_follow_the_naming_convention():
    files = init_db.migration_files()
    assert files, "db/migrations/ is empty"
    assert files[0].name == "000_baseline.sql"
    numbers = [int(p.name[:3]) for p in files]
    assert numbers == list(range(len(files))), f"gaps in migration numbers: {numbers}"


@pytest.mark.parametrize("bad", ["03_add_x.sql", "003-add-x.sql", "003_Add_X.sql", "notes.txt"])
def test_misnamed_file_is_refused_not_skipped(tmp_path, bad):
    (tmp_path / "000_baseline.sql").write_text("SELECT 1;")
    (tmp_path / bad).write_text("SELECT 1;")
    with pytest.raises(ValueError, match="not named"):
        init_db.migration_files(tmp_path)


def test_duplicate_number_is_refused(tmp_path):
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "001_b.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="same number"):
        init_db.migration_files(tmp_path)


# ----------------------------------------------------------------- database

@pytest.fixture
def empty_db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine(url)

    def wipe():
        with engine.begin() as c:
            c.exec_driver_sql(
                "DROP VIEW IF EXISTS alert_probs; "
                "DROP TABLE IF EXISTS alerts, runs, daily_students, schema_migrations CASCADE"
            )

    wipe()
    yield engine
    wipe()
    engine.dispose()


def _migrate(engine, files=None):
    with engine.begin() as c:
        return init_db.apply_migrations(c, files or init_db.migration_files())


def _recorded(engine):
    with engine.connect() as c:
        return sorted(r[0] for r in c.exec_driver_sql("SELECT version FROM schema_migrations"))


def test_fresh_database_runs_everything_once(empty_db):
    files = init_db.migration_files()
    assert _migrate(empty_db) == [p.name for p in files]
    assert _migrate(empty_db) == []  # second init: nothing pending
    assert _recorded(empty_db) == [p.name[:3] for p in files]


def test_init_survives_a_view_on_alerts(empty_db):
    """The exact failure from the issue: 001 re-issuing ALTER TYPE under a view."""
    _migrate(empty_db)
    with empty_db.begin() as c:
        c.exec_driver_sql("CREATE VIEW alert_probs AS SELECT churn_probability FROM alerts")
    assert _migrate(empty_db) == []
    # And the guarded file itself is safe to run by hand, view or not.
    with empty_db.begin() as c:
        c.exec_driver_sql((init_db.MIGRATIONS_DIR / "001_churn_probability_to_double.sql").read_text())


def test_untracked_legacy_database_is_adopted(empty_db):
    """A database from before B-16: NUMERIC column, old index, no tracking table."""
    with empty_db.begin() as c:
        c.exec_driver_sql(
            """
            CREATE TABLE daily_students (
                student_id TEXT PRIMARY KEY, enrollment_date DATE,
                features JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
            CREATE TABLE alerts (
                id SERIAL PRIMARY KEY,
                student_id TEXT NOT NULL REFERENCES daily_students(student_id),
                run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                churn_probability NUMERIC(5,4) NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('new', 'still_at_risk')),
                top_reasons TEXT, top_reasons_detail JSONB);
            CREATE INDEX alerts_run_at_idx ON alerts (run_at);
            INSERT INTO daily_students VALUES ('S1', NULL, '{}', now());
            INSERT INTO alerts (student_id, churn_probability, status) VALUES ('S1', 0.6486, 'new');
            """
        )
    _migrate(empty_db)
    with empty_db.connect() as c:
        dtype = c.exec_driver_sql(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='alerts' AND column_name='churn_probability'"
        ).scalar()
        indexes = {r[0] for r in c.exec_driver_sql("SELECT indexname FROM pg_indexes WHERE tablename='alerts'")}
        kept = c.exec_driver_sql("SELECT count(*) FROM alerts").scalar()
    assert dtype == "double precision"
    # 002 created the (run_at, student_id) index; 004 replaced it with one per run.
    assert "alerts_run_id_student_id_uidx" in indexes
    assert not indexes & {"alerts_run_at_idx", "alerts_run_at_student_id_uidx"}
    assert kept == 1  # adopting never drops data
    assert _migrate(empty_db) == []


def test_history_migration_keeps_rows_and_allows_new_snapshots(empty_db):
    """003 on a database that already holds data: rows keep the date they were loaded
    (updated_at), a second snapshot of the same student fits, and alerts no longer
    need a snapshot row."""
    before = [p for p in init_db.migration_files() if p.name < "003"]
    _migrate(empty_db, before)
    with empty_db.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO daily_students VALUES ('S1', NULL, '{}', now() - interval '21 days')"
        )
        c.exec_driver_sql(
            "INSERT INTO alerts (student_id, churn_probability, status) VALUES ('S1', 0.6, 'new')"
        )

    _migrate(empty_db, [p for p in init_db.migration_files() if p.name < "004"])  # 003 only

    with empty_db.begin() as c:
        carried = c.exec_driver_sql(
            "SELECT as_of_date = updated_at::date AND as_of_date <= CURRENT_DATE - 20 "
            "FROM daily_students WHERE student_id = 'S1'"
        ).scalar()
        c.exec_driver_sql(
            "INSERT INTO daily_students (student_id, as_of_date, features) "
            "VALUES ('S1', CURRENT_DATE - 7, '{}')"
        )
        c.exec_driver_sql(
            "INSERT INTO alerts (student_id, churn_probability, status) VALUES ('GONE', 0.5, 'new')"
        )
        total = c.exec_driver_sql("SELECT count(*) FROM daily_students").scalar()
        kept = c.exec_driver_sql("SELECT count(*) FROM alerts").scalar()
    assert carried is True
    assert total == 2
    assert kept == 2

def test_runs_migration_gives_every_legacy_alert_a_run(empty_db):
    """004 on alerts written before `runs` existed: one run per distinct run_at, the
    last one of a day stays 'ok' and earlier ones on that day become 'superseded'."""
    _migrate(empty_db, [p for p in init_db.migration_files() if p.name < "004"])
    with empty_db.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO alerts (student_id, run_at, churn_probability, status) VALUES "
            "('A', '2026-09-01 06:00+00', 0.6, 'new'), "
            "('B', '2026-09-01 06:00+00', 0.7, 'new'), "
            "('A', '2026-09-01 08:00+00', 0.6, 'still_at_risk'), "  # same day, re-run
            "('A', '2026-09-02 06:00+00', 0.6, 'still_at_risk')"
        )

    _migrate(empty_db)

    with empty_db.connect() as c:
        runs = c.exec_driver_sql(
            "SELECT run_date::text, status, at_risk_count FROM runs ORDER BY started_at"
        ).fetchall()
        orphans = c.exec_driver_sql("SELECT count(*) FROM alerts WHERE run_id IS NULL").scalar()
        linked = c.exec_driver_sql(
            "SELECT count(*) FROM alerts a JOIN runs r USING (run_id) WHERE a.run_at = r.started_at"
        ).scalar()
    assert runs == [
        ("2026-09-01", "superseded", 2),
        ("2026-09-01", "ok", 1),
        ("2026-09-02", "ok", 1),
    ]
    assert orphans == 0
    assert linked == 4


def test_a_database_built_from_schema_sql_can_be_adopted(empty_db):
    """schema.sql applied directly gives an untracked database already in the current
    shape; init_db must adopt it without 003 failing on what is already there."""
    with empty_db.begin() as c:
        c.exec_driver_sql(SCHEMA_SQL)
        c.exec_driver_sql(
            "INSERT INTO daily_students (student_id, as_of_date, features) "
            "VALUES ('S1', CURRENT_DATE, '{}')"
        )
    _migrate(empty_db)
    assert _migrate(empty_db) == []
    with empty_db.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM daily_students").scalar() == 1


def test_failed_migration_rolls_back_and_records_nothing(empty_db, tmp_path):
    good = init_db.migration_files()
    (tmp_path / "900_broken.sql").write_text("ALTER TABLE no_such_table ADD COLUMN x INT;")
    with pytest.raises(Exception):
        _migrate(empty_db, good + [tmp_path / "900_broken.sql"])
    with empty_db.connect() as c:
        tracked = c.exec_driver_sql("SELECT to_regclass('public.schema_migrations')").scalar()
    assert tracked is None  # the whole call rolled back, not just the broken file


def _shape(engine):
    """Everything about the app tables that a migration could change."""
    with engine.connect() as c:
        columns = c.exec_driver_sql(
            "SELECT table_name, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name <> 'schema_migrations' ORDER BY 1, 2"
        ).fetchall()
        indexes = c.exec_driver_sql(
            "SELECT tablename, indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename <> 'schema_migrations' ORDER BY 1, 2"
        ).fetchall()
        constraints = c.exec_driver_sql(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace "
            "AND conrelid::regclass::text <> 'schema_migrations' ORDER BY 1, 2"
        ).fetchall()
    return columns, indexes, constraints


def test_schema_sql_matches_the_migrations(empty_db):
    """Edit one without the other and this fails - that is its whole job."""
    _migrate(empty_db)
    from_migrations = _shape(empty_db)

    with empty_db.begin() as c:
        c.exec_driver_sql("DROP TABLE alerts, runs, daily_students, schema_migrations CASCADE")
        c.exec_driver_sql(SCHEMA_SQL)
    from_schema_sql = _shape(empty_db)

    assert from_migrations == from_schema_sql
