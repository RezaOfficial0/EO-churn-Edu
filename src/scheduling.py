"""When the daily run happens, and how we know it still happens (B-14).

Two things live here, both deliberately free of any process, network or clock
side effects so they can be tested without sleeping or waiting for a real 09:00:

  - the SCHEDULE: `RUN_AT` + `SCHEDULER_TIMEZONE` + `RUN_DAYS` -> the next instant
    the run is due. `scripts/scheduler.py` sleeps until that instant;
  - the STATE: a small JSON file recording the last successful run, the last
    failure, and the last heartbeat alert - which is what turns "no message
    arrived this morning" from a guess into a fact.

Why a file and not a table: the schema has exactly two tables, `daily_students`
and `alerts`. `alerts` is append-only customer history and a synthetic row saying
"a run happened" would corrupt it (and the `new` / `still_at_risk` baseline with
it). The `runs` table that should hold this is B-04. Until it exists the file is
the only place a run's outcome can be written without lying to the customer's
data; it lives on a named volume so it survives `docker compose up --build` and
a container restart. See `record_success` / `record_failure` for the B-04 hook.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# How far ahead next_run_at() is willing to look. RUN_DAYS can exclude at most six
# consecutive days, so eight covers every valid configuration with room to spare;
# a bound (rather than `while True`) means a bug in the day set cannot hang the
# scheduler in a loop that never sleeps.
_MAX_DAYS_AHEAD = 8

_DAY_NAMES = {1: "Pzt", 2: "Sal", 3: "Çar", 4: "Per", 5: "Cum", 6: "Cmt", 7: "Paz"}


class ScheduleConfigError(ValueError):
    """RUN_AT / RUN_DAYS / SCHEDULER_TIMEZONE cannot be understood.

    Raised at startup, never mid-loop: a scheduler that "helpfully" falls back to
    some default time when RUN_AT is a typo is a scheduler nobody can trust. The
    container must fail loudly instead, while whoever edited .env is still looking.
    """


# --- The schedule -----------------------------------------------------------
def parse_run_at(value: str) -> time:
    """"09:00" -> time(9, 0). Raises ScheduleConfigError on anything else."""
    text = (value or "").strip()
    if not text:
        raise ScheduleConfigError("RUN_AT is empty (expected HH:MM, e.g. 09:00)")
    # time.fromisoformat accepts "9" and "0900"; both are far likelier to be a
    # typo than an intention, so the format is pinned to HH:MM(:SS).
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise ScheduleConfigError(
            f"RUN_AT={text!r} is not a time of day (expected HH:MM, e.g. 09:00)"
        )
    try:
        return time.fromisoformat(text)
    except ValueError:
        raise ScheduleConfigError(
            f"RUN_AT={text!r} is not a valid time of day (hour 00-23, minute 00-59)"
        ) from None


def parse_run_days(value: str) -> frozenset[int]:
    """"1-5" / "1,3,5" / "" -> the ISO weekdays (Mon=1 .. Sun=7) the run is due on.

    Empty means every day. The old README schedule was a weekday-only cron line,
    so dropping the concept would have silently started messaging mentors on
    Sundays the day this service replaced it.
    """
    text = (value or "").strip()
    if not text:
        return frozenset(range(1, 8))

    days: set[int] = set()
    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            if "-" in piece:
                start, end = (int(part) for part in piece.split("-", 1))
                if start > end:
                    raise ValueError
                days.update(range(start, end + 1))
            else:
                days.add(int(piece))
        except ValueError:
            raise ScheduleConfigError(
                f"RUN_DAYS={text!r} is not a weekday list (expected e.g. 1-5, or 1,3,5; "
                "Mon=1 .. Sun=7)"
            ) from None

    outside = sorted(day for day in days if not 1 <= day <= 7)
    if outside:
        raise ScheduleConfigError(f"RUN_DAYS={text!r} has day(s) outside 1-7: {outside}")
    if not days:
        raise ScheduleConfigError(f"RUN_DAYS={text!r} selects no days at all")
    return frozenset(days)


def resolve_timezone(name: str) -> tzinfo:
    """The IANA zone the run time is expressed in.

    A missing zone database is a configuration error, not something to paper over
    with UTC: Europe/Istanbul is UTC+3, so a silent fallback would move the run to
    06:00 local and nobody would notice until a customer asked why.
    """
    text = (name or "").strip()
    if not text:
        raise ScheduleConfigError("SCHEDULER_TIMEZONE is empty (e.g. Europe/Istanbul)")
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleConfigError(
            f"SCHEDULER_TIMEZONE={text!r} is not a known IANA time zone "
            "(e.g. Europe/Istanbul, UTC). On a slim image this can also mean the "
            "tzdata package is missing from the image."
        ) from None


def next_run_at(
    now: datetime,
    *,
    run_at: time,
    tz: tzinfo,
    run_days: frozenset[int] | None = None,
) -> datetime:
    """The first instant at or after `now` when the run is due, as an aware datetime.

    Strictly after `now`: called immediately after a run finished at exactly
    RUN_AT, returning "now" would run the pipeline twice in one day.

    DST is handled by comparing instants rather than wall clocks. The candidate is
    built as a wall-clock time in `tz` and `fold=0` is left at its default, which
    picks the FIRST of the two occurrences on a fall-back day - so an ambiguous
    RUN_AT runs once, early, instead of twice. On a spring-forward day a RUN_AT
    inside the missing hour does not exist locally; zoneinfo maps it to a real
    instant (one hour later in wall-clock terms) and the run still happens that
    day, which is what an operator expecting a daily alert wants.
    """
    days = run_days if run_days is not None else frozenset(range(1, 8))
    local_now = now.astimezone(tz)

    for offset in range(_MAX_DAYS_AHEAD):
        day = (local_now + timedelta(days=offset)).date()
        if day.isoweekday() not in days:
            continue
        candidate = datetime.combine(day, run_at, tzinfo=tz)
        if candidate > now:
            return candidate

    # Unreachable for any day set parse_run_days() accepts; an assertion would be
    # optimised away with -O, and a scheduler that cannot say when it next runs
    # must stop rather than sleep forever.
    raise ScheduleConfigError(
        f"no run time found within {_MAX_DAYS_AHEAD} days for RUN_DAYS={sorted(days)}"
    )


def seconds_until(now: datetime, target: datetime) -> float:
    """Never negative: a target already in the past means "run immediately"."""
    return max(0.0, (target - now).total_seconds())


def describe_schedule(*, run_at: time, tz: tzinfo, run_days: frozenset[int]) -> str:
    """One log line an operator can check against what they meant to configure."""
    if run_days == frozenset(range(1, 8)):
        days = "her gün"
    else:
        days = ", ".join(_DAY_NAMES[day] for day in sorted(run_days))
    return f"{run_at.strftime('%H:%M')} {tz} ({days})"


# --- The state file ---------------------------------------------------------
def read_state(path: str | Path) -> dict:
    """The recorded state, or {} if there is none yet or it is unreadable.

    Unreadable is treated as empty on purpose: a corrupted state file must not
    stop the daily run. It costs one spurious heartbeat alert, which is the safe
    direction to fail in.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("scheduler state at %s unreadable (%s); treating as empty", path, e)
        return {}
    return state if isinstance(state, dict) else {}


def write_state(path: str | Path, state: dict) -> None:
    """Replace the state file atomically, so a crash mid-write cannot truncate it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same directory as the target: os.replace is only atomic within one filesystem,
    # and the state lives on a mounted volume while /tmp does not.
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, prefix=target.name + ".", delete=False
    )
    try:
        with handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def parse_timestamp(value) -> datetime | None:
    """An ISO-8601 string from the state file back into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def note_started(path: str | Path, *, now: datetime) -> dict:
    """Record that the scheduler is up, and when it first ever came up.

    `first_seen_at` is what the heartbeat measures against before the first
    successful run: without it a brand-new deployment would either alert
    instantly ("no success in 24h" - correct but useless) or never.
    """
    state = read_state(path)
    state.setdefault("first_seen_at", _stamp(now))
    state["last_started_at"] = _stamp(now)
    write_state(path, state)
    return state


def record_success(path: str | Path, *, now: datetime) -> dict:
    """Mark the run that just finished as successful.

    B-04 hook: when the `runs` table exists, this is where the matching
    UPDATE runs SET status='ok', finished_at=now WHERE id=<this run> belongs. The
    file stays regardless - it is also what the container healthcheck reads, and
    that must not need a database connection.
    """
    state = read_state(path)
    state["last_success_at"] = _stamp(now)
    state.pop("last_failure_at", None)
    state.pop("last_failure_step", None)
    state.pop("last_failure_message", None)
    write_state(path, state)
    return state


def record_failure(
    path: str | Path, *, now: datetime, step: str, message: str
) -> dict:
    """Mark the run that just failed, with which half failed and why.

    B-04 hook: this is the "write status='failed' to runs" of the issue. The
    `runs` table does not exist yet, so the same three facts (when, which step,
    what it said) are written here instead; `message` is already redacted by the
    caller.
    """
    state = read_state(path)
    state["last_failure_at"] = _stamp(now)
    state["last_failure_step"] = step
    # Bounded: this file is read by the healthcheck on every interval, and a
    # runaway traceback must not turn it into a megabyte of JSON.
    state["last_failure_message"] = message[:2000]
    write_state(path, state)
    return state


def record_heartbeat_alert(path: str | Path, *, now: datetime) -> dict:
    state = read_state(path)
    state["last_heartbeat_alert_at"] = _stamp(now)
    write_state(path, state)
    return state


def heartbeat_reason(
    *,
    now: datetime,
    last_success_at: datetime | None,
    first_seen_at: datetime | None = None,
    last_alert_at: datetime | None = None,
    max_hours: float = 24.0,
) -> str | None:
    """Why an ops heartbeat alert is due, or None if it is not.

    Pure: every input is passed in, so the decision is testable without a clock,
    a file or a network. The rules, in order:

      1. a success inside the window   -> silent (the normal case);
      2. never a success, but the scheduler has only just been deployed -> silent,
         measured from `first_seen_at`, so `up -d` does not alert before 09:00;
      3. otherwise overdue -> alert, unless one was already sent inside the same
         window. Without that last clause a crash-restart loop would send one
         alert per restart, and an ops channel nobody can stand is an ops channel
         nobody reads.
    """
    window = timedelta(hours=max_hours)

    if last_success_at is not None and now - last_success_at <= window:
        return None
    if last_success_at is None and first_seen_at is not None and now - first_seen_at <= window:
        return None
    if last_alert_at is not None and now - last_alert_at <= window:
        return None

    if last_success_at is None:
        return (
            f"Hiç başarılı koşu kaydı yok ({max_hours:.0f} saatten uzun süredir "
            "zamanlayıcı ayakta)."
        )
    hours = (now - last_success_at).total_seconds() / 3600
    return (
        f"Son başarılı koşu {hours:.0f} saat önce "
        f"({last_success_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC), "
        f"sınır {max_hours:.0f} saat."
    )
