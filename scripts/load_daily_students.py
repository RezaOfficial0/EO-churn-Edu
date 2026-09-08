"""Load today's students from a CSV into the `daily_students` table.

This is how data gets INTO the database. Without it, `DATA_SOURCE=db` can only
read rows someone inserted by hand.

    python scripts/load_daily_students.py                    # loads data/daily_data.csv
    python scripts/load_daily_students.py path/to/other.csv

It needs `DATABASE_URL` and nothing else - `DATA_SOURCE` is irrelevant here, since
loading into Postgres is the point whether or not the pipeline is currently reading
from it. Run it before flipping `DATA_SOURCE` to "db", not after.

Re-running is safe: students are matched on `student_id` and updated in place, so
loading the same file twice leaves the table identical rather than doubled.

The CSV is validated first (same gate the daily pipeline uses), so a file with a
missing column, a stray extra column or a duplicate `student_id` is rejected before
anything is written.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DAILY_DATA_PATH, STUDENT_INFO
from src.data.features import RAW_FEATURE_COLUMNS
from src.data.loader import count_daily_students_db, data_loader, upsert_daily_students_db
from src.data.validation import DataValidationError, validate
from src.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "csv",
        nargs="?",
        default=DAILY_DATA_PATH,
        help=f"CSV to load (default: {DAILY_DATA_PATH})",
    )
    args = parser.parse_args()

    configure_logging()

    try:
        raw = data_loader(args.csv)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        # max_null_ratio=1.0: raw data is allowed nulls in the columns the pipeline
        # imputes. The checks that matter here are the structural ones - required
        # columns present, no stray extra column, no duplicate student_id.
        raw = validate(raw, STUDENT_INFO + RAW_FEATURE_COLUMNS, max_null_ratio=1.0)
    except DataValidationError as e:
        print(f"error: {args.csv} did not pass validation, nothing was written.\n{e}", file=sys.stderr)
        return 1

    try:
        before = count_daily_students_db()
        written = upsert_daily_students_db(raw)
        after = count_daily_students_db()
    except RuntimeError as e:  # DATABASE_URL not set
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(
        f"{written} row(s) from {args.csv} written to daily_students "
        f"({after - before} new, {written - (after - before)} updated); "
        f"{after} student(s) in the table."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
