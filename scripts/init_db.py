"""Create (or update) the database schema, without needing the `psql` command.

`db/schema.sql` is the source of truth for the schema; this applies it, then every
file in `db/migrations/` in name order. Both are written to be safe to re-run, so
this command is safe to re-run too:

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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import BASE_DIR, DATABASE_URL
from src.data.loader import _get_engine, _sql

SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
MIGRATIONS_DIR = BASE_DIR / "db" / "migrations"

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


def apply_sql_file(connection, path: Path) -> None:
    # exec_driver_sql, not text(): these files hold several statements, and the
    # driver runs them as one script the way psql would.
    connection.exec_driver_sql(path.read_text())
    print(f"  applied  {path.relative_to(BASE_DIR)}")


def verify(connection) -> list[str]:
    problems = []
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
        with engine.begin() as connection:
            if not args.verify:
                print(f"Applying schema to {engine.url.database}:")
                apply_sql_file(connection, SCHEMA_PATH)
                for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    apply_sql_file(connection, migration)
                print()

            print("Verifying:")
            problems = verify(connection)
    except Exception as e:  # noqa: BLE001 - a connection error here is the answer
        if "does not exist" in str(e) and database:
            return report_missing_database(database)
        print(f"error: could not reach the database.\n{type(e).__name__}: {e}", file=sys.stderr)
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
