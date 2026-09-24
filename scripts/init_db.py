"""Create (or update) the database schema, without needing the `psql` command.

Applies every file in `db/migrations/` that this database has not seen yet, in
number order, and records each one in `schema_migrations`. A migration runs ONCE
per database, so it does not have to be written to survive being re-run - that
was the old contract, and `001` broke it (B-16). Safe to re-run: with nothing
pending it changes nothing.

`db/schema.sql` is the readable picture of the current schema, not something this
applies. `000_baseline.sql` is its frozen starting point; every later change is a
new `NNN_short_name.sql` file AND the same edit in `schema.sql`.
tests/test_migrations.py fails if the two ever describe different databases.

    python scripts/init_db.py              # apply schema + migrations, then verify
    python scripts/init_db.py --verify     # only check what is already there
    python scripts/init_db.py --create-db  # also create the database if it is missing

It talks to `DATABASE_URL` through the same driver the application uses, so if this
works, the pipeline's connection works.

Creating the database is opt-in (`--create-db`) rather than automatic: a typo in
DATABASE_URL would otherwise produce a mysteriously empty database instead of an
error. Without the flag, a missing database is reported along with the databases
that DO exist on that server - which is usually enough to spot the typo, or to
notice you are pointed at a different Postgres instance than you thought.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import BASE_DIR, DATABASE_URL
from src.data.loader import _get_engine, _sql

MIGRATIONS_DIR = BASE_DIR / "db" / "migrations"

# `001_churn_probability_to_double.sql`: three digits, then lowercase words.
MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

# Any fixed number works; it only has to be the same in every init_db process.
# Two `init` containers starting at once would otherwise both see "001 pending".
ADVISORY_LOCK_KEY = 461_600_16

TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# What the application expects to find. Checked after applying, so a database that
# drifted (edited by hand, restored from an old dump) is reported instead of
# failing later inside the pipeline.
EXPECTED = {
    "daily_students": {"student_id", "enrollment_date", "features", "updated_at"},
    "alerts": {
        "id",
        "student_id",
        "run_at",
        "churn_probability",
        "status",
        "top_reasons",
        "top_reasons_detail",
    },
}


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """Every migration, in order. Refuses a directory it cannot order unambiguously.

    A misnamed file is an error rather than something to skip: `03_add_x.sql` or
    `003-add-x.sql` silently never running is exactly the failure this prevents.
    """
    files = sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))
    bad = [p.name for p in files if not MIGRATION_NAME.match(p.name)]
    if bad:
        raise ValueError(
            f"db/migrations/ has file(s) not named NNN_lowercase_words.sql: {bad}"
        )
    numbers = [p.name[:3] for p in files]
    duplicated = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicated:
        raise ValueError(f"db/migrations/ has two files with the same number: {duplicated}")
    return files


def applied_versions(connection) -> set[str] | None:
    """Versions already recorded, or None if this database has never been tracked."""
    exists = connection.execute(_sql("SELECT to_regclass('public.schema_migrations')")).scalar()
    if exists is None:
        return None
    return {row[0] for row in connection.execute(_sql("SELECT version FROM schema_migrations"))}


def apply_migrations(connection, files: list[Path]) -> list[str]:
    """Run the files this database has not recorded yet. Returns their names.

    Runs inside the caller's transaction: a failing migration rolls back itself,
    every migration before it in this call, and their schema_migrations rows, so
    the database is never left half-migrated and the next run retries cleanly.
    """
    connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})")
    connection.exec_driver_sql(TRACKING_TABLE_SQL)
    done = applied_versions(connection) or set()

    applied = []
    for path in files:
        version = path.name[:3]
        if version in done:
            continue
        # exec_driver_sql, not text(): these files hold several statements, and
        # the driver runs them as one script the way psql would.
        connection.exec_driver_sql(path.read_text())
        connection.execute(
            _sql("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
            {"v": version, "n": path.name},
        )
        applied.append(path.name)
    return applied


def verify(connection, files: list[Path] | None = None) -> list[str]:
    problems = []
    if files is not None:
        done = applied_versions(connection)
        if done is None:
            problems.append("schema_migrations is missing - migrations were never tracked here")
        else:
            pending = [p.name for p in files if p.name[:3] not in done]
            if pending:
                problems.append(f"migration(s) not applied yet: {pending}")
    rows = connection.execute(
        _sql(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
        ),
        {"tables": list(EXPECTED)},
    ).fetchall()

    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)

    for table, expected_columns in EXPECTED.items():
        if table not in found:
            problems.append(f"table `{table}` is missing")
            continue
        missing = expected_columns - found[table]
        if missing:
            problems.append(f"table `{table}` is missing column(s): {sorted(missing)}")
        else:
            print(f"  ok       {table} ({len(found[table])} columns)")

    if "alerts" in found:
        column_type = connection.execute(
            _sql(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'alerts' AND column_name = 'churn_probability'"
            )
        ).scalar()
        if column_type == "numeric":
            problems.append(
                "alerts.churn_probability is NUMERIC - it rounds probabilities. "
                "Apply db/migrations/001_churn_probability_to_double.sql "
                "(re-running this command does it)."
            )

    return problems


def _server_engine():
    """An engine pointed at the server's default `postgres` database.

    Used to look around when the target database is missing - you cannot ask a
    database that does not exist what its neighbours are called.
    """
    from sqlalchemy import create_engine

    return create_engine(_get_engine().url.set(database="postgres"), isolation_level="AUTOCOMMIT")


def list_databases() -> list[str]:
    with _server_engine().connect() as connection:
        return [
            row[0]
            for row in connection.execute(
                _sql("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname")
            )
        ]


def create_database(name: str) -> None:
    from sqlalchemy.sql import quoted_name

    with _server_engine().connect() as connection:
        # An identifier cannot be a bind parameter, so it is quoted instead.
        connection.exec_driver_sql(f'CREATE DATABASE "{quoted_name(name, True)}"')


def report_missing_database(name: str) -> int:
    print(f"error: the database `{name}` does not exist on this server.", file=sys.stderr)
    try:
        existing = list_databases()
    except Exception:  # noqa: BLE001 - the connection itself is the problem, already reported
        return 1
    print(f"\nDatabases on this server: {', '.join(existing)}", file=sys.stderr)
    print(
        f"\nEither DATABASE_URL points at the wrong server or the wrong name, "
        f"or `{name}` was never created here. To create it:\n"
        f"    python scripts/init_db.py --create-db",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify", action="store_true", help="only check the schema, change nothing"
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="create the database named in DATABASE_URL if it does not exist",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        print(
            "error: DATABASE_URL is not set. Put it in .env (see .env.example).",
            file=sys.stderr,
        )
        return 1

    engine = _get_engine()
    database = engine.url.database

    if args.create_db:
        try:
            if database in list_databases():
                print(f"Database {database} already exists.")
            else:
                create_database(database)
                print(f"Created database {database}.")
        except Exception as e:  # noqa: BLE001
            print(f"error: could not create `{database}`.\n{type(e).__name__}: {e}", file=sys.stderr)
            return 1

    try:
        files = migration_files()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        with engine.begin() as connection:
            if not args.verify:
                print(f"Migrating {engine.url.database}:")
                applied = apply_migrations(connection, files)
                for name in applied:
                    print(f"  applied  db/migrations/{name}")
                if not applied:
                    print("  nothing to apply - already up to date")
                print()

            print("Verifying:")
            problems = verify(connection, files)
    except Exception as e:  # noqa: BLE001 - a connection error here is the answer
        if "does not exist" in str(e) and database:
            return report_missing_database(database)
        # Either the connection failed or a migration did. A failed migration was
        # rolled back together with everything this run applied, so fixing the
        # file and re-running is always safe.
        print(f"error: the database step failed.\n{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print()
    if problems:
        print("Schema is NOT ready:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Schema is ready. Next: python scripts/load_daily_students.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
