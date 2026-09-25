"""The last run's data-quality summary, handed from the pipeline to the messenger.

The daily chain is two processes (`scheduler.py` runs them in order):

    python -m pipeline.daily_pipeline   &&   python scripts/send_daily_alerts.py

Only the first one knows how many rows it had to quarantine (B-28); only the second
one builds the message a mentor reads. The second reads the alert log, not the daily
data, so it cannot recompute the number - it has to be told. This module is that
hand-off: one small JSON file of COUNTS, never a student id, never a row.

Deliberately not the alert log: a run that quarantined rows and found nobody at risk
writes no alert rows at all, and that is exactly the run whose skipped count matters.
Deliberately not the scheduler state file either - that one belongs to
`scripts/scheduler.py`, which rewrites it around the subprocess this file is written
inside, and two writers on one file is a race nobody needs. Same directory, so the
same named volume in compose already persists it.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.data.validation import QuarantineReport

logger = logging.getLogger(__name__)


def write_report(path: str | Path, report: QuarantineReport, *, run_at=None) -> None:
    """Replace the run-quality file with `report`, atomically.

    Never raises: a read-only state directory must not turn a successful run into a
    failed one. The count is then missing from the message, which is a smaller loss
    than no message at all - and `logger.warning` in `check_quarantine` has already
    recorded it where an operator can find it.
    """
    run_at = run_at or datetime.now(timezone.utc)
    record = {"run_at": run_at.isoformat(), **report.as_record()}
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same directory as the target: os.replace is only atomic within one
        # filesystem, and this path is a mounted volume while /tmp is not.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, prefix=target.name + ".", delete=False
        )
        try:
            with handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(handle.name, target)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
    except OSError as e:
        logger.warning("could not write the run-quality record to %s (%s)", path, e)


def read_report(path: str | Path) -> QuarantineReport | None:
    """The last run's report, or None if there is none / it cannot be read.

    None means "nothing to say", not "nothing was skipped": the caller adds a line
    to the message only when there IS a report, so a missing file leaves the message
    exactly as it was before B-28.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("run-quality record at %s unreadable (%s)", path, e)
        return None
    if not isinstance(record, dict):
        return None
    try:
        return QuarantineReport.from_record(record)
    except (TypeError, ValueError) as e:
        logger.warning("run-quality record at %s has unusable counts (%s)", path, e)
        return None
