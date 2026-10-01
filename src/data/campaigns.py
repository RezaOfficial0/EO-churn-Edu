"""Store and read back the retention campaigns a generator writes to this API.

The campaign generator is a SEPARATE service: it reads scored students over
`GET /students`, drafts a campaign per student, and posts the result back here.
This module is the store in between, so the dashboard can show a mentor the
campaign next to the student who caused it.

Append-only, one JSON object per line. Two reasons:

  * A campaign is nested (steps, success criteria, citations). Flattening that
    into CSV columns loses exactly the structure the dashboard renders.
  * The file is a LOG, not a table. Regenerating a campaign must not destroy the
    previous one - "what did we tell this student last week, and what did the
    mentor actually do" is the question the pilot has to answer later (B-06).
    Readers take the newest record per student; nothing overwrites.

`DATA_SOURCE=db` has no campaign table yet, so this is the CSV-side store only.
The read/write pair is deliberately the same shape as `append_to_alert_log` /
`previous_at_risk_ids` in loader.py, so a `campaigns` table can be added behind
the same two functions without touching the API or the dashboard.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone

from config import CAMPAIGNS_PATH

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve(campaigns_path: str | None) -> str:
    """Pick the log path at CALL time, not at import time.

    `def f(path=CAMPAIGNS_PATH)` would bind the configured path into the function
    object when this module is first imported. Nothing could redirect it
    afterwards: a test that points the store at a tmp_path would still be writing
    to - and reading from - the real `data/campaigns.jsonl`, and would pass or
    fail depending on what a demo run left there. Reading the module global here
    keeps the default overridable.
    """
    return campaigns_path or CAMPAIGNS_PATH


def append_campaign(campaign: dict, campaigns_path: str | None = None) -> dict:
    """Append one campaign and return the stored record.

    `stored_at` is set here, not taken from the request: the generator's clock is
    not ours, and the ordering readers depend on has to come from one clock.
    """
    campaigns_path = _resolve(campaigns_path)
    record = {**campaign, "stored_at": _utc_now()}
    line = json.dumps(record, ensure_ascii=False, allow_nan=False)
    if "\n" in line:  # json.dumps escapes newlines; this is a guard, not a hope
        raise ValueError("campaign serialised to more than one line")

    directory = os.path.dirname(campaigns_path) or "."
    os.makedirs(directory, exist_ok=True)
    # Single write of one line, on a file opened in append mode: concurrent
    # writers interleave records, never halves of a record.
    with open(campaigns_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def read_campaigns(campaigns_path: str | None = None) -> list[dict]:
    """Every stored campaign, oldest first. Missing file means none yet, not an error."""
    campaigns_path = _resolve(campaigns_path)
    if not os.path.exists(campaigns_path):
        return []
    records = []
    with open(campaigns_path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # One torn line (a killed process mid-write) must not take out the
                # whole panel. Skipped and counted, never guessed at.
                logger.warning("campaign log line %d is not valid JSON, skipped", number)
    return records


def latest_campaign_by_student(campaigns_path: str | None = None) -> dict[str, dict]:
    """Newest campaign per student. Later lines win, because the file is a log."""
    latest: dict[str, dict] = {}
    for record in read_campaigns(campaigns_path):
        student_id = record.get("student_id")
        if isinstance(student_id, str) and student_id:
            latest[student_id] = record
    return latest


def clear_campaigns(campaigns_path: str | None = None) -> int:
    """Truncate the log. Demo-reset only; returns how many records were dropped.

    Writes a new empty file and replaces the old one, so a reader that opens the
    path during the reset sees either the full log or an empty one, never a
    half-truncated file.
    """
    campaigns_path = _resolve(campaigns_path)
    dropped = len(read_campaigns(campaigns_path))
    directory = os.path.dirname(campaigns_path) or "."
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False
    )
    handle.close()
    os.replace(handle.name, campaigns_path)
    return dropped
