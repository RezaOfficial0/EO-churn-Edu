"""Run the daily chain on a schedule, and shout if it fails (B-14).

    python scripts/scheduler.py                 # the loop (what the compose service runs)
    python scripts/scheduler.py --once          # run the chain now and exit
    python scripts/scheduler.py --next 5        # print the next 5 due times, run nothing
    python scripts/scheduler.py --healthcheck   # exit 1 if the last run failed / is stale

The chain, in this order and only in this order:

    python -m pipeline.daily_pipeline   &&   python scripts/send_daily_alerts.py

The `&&` is the point. If scoring fails, nobody gets a message: a mentor reading
"bugün risk altında öğrenci yok" cannot tell it apart from a run that never
happened, and the second is the one that costs a student. Two `subprocess.run`
calls rather than one `sh -c "a && b"` because the ops alert has to name WHICH half
failed and quote what it said; the ordering guarantee is identical - the second
command is only started when the first returned 0.

Why this instead of cron-in-a-container or an off-the-shelf scheduler image:

  - the whole schedule is `RUN_AT` + `SCHEDULER_TIMEZONE` + `RUN_DAYS` in .env, so
    there is no second configuration language (crontab, ofelia labels) to keep in
    step with it, and no image whose failure modes we would have to learn;
  - cron in a container is famously silent: it has its own stripped environment, it
    mails failures nowhere, and a mistyped line just never fires. Every failure
    here is a Python exception on stdout that `docker compose logs` shows;
  - "sleep until the next occurrence" is one function, `src.scheduling.next_run_at`,
    which is pure and therefore tested (including DST) without waiting for 09:00.

The cost of a sleeping loop, honestly: a missed window while the container is down.
If the machine is off at 09:00 the run does not happen at 09:15 when it comes back
- the scheduler computes tomorrow's time. That is exactly what the heartbeat exists
to catch, and catching it loudly is better than a surprise run at an arbitrary hour.

This process talks to the database and the model directly, never over HTTP, so it
needs no API key and B-07's fail-closed rule is untouched by it.
"""
import argparse
import logging
import subprocess
import sys
import time as time_module
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config import (  # noqa: E402
    RUN_AT,
    RUN_DAYS,
    SCHEDULER_HEARTBEAT_HOURS,
    SCHEDULER_RUN_TIMEOUT_SECONDS,
    SCHEDULER_STATE_PATH,
    SCHEDULER_TIMEZONE,
)
from src import scheduling  # noqa: E402
from src.logging_setup import configure_logging  # noqa: E402
from src.notifications import channels  # noqa: E402
from src.notifications.ops import ops_channels_configured, send_ops_alert  # noqa: E402

logger = logging.getLogger(__name__)

# The chain. Names are what an ops message says failed, so they are short and
# human: "pipeline" and "alerts", not module paths.
STEPS: tuple[tuple[str, list[str]], ...] = (
    ("pipeline", [sys.executable, "-m", "pipeline.daily_pipeline"]),
    ("alerts", [sys.executable, str(REPO_ROOT / "scripts" / "send_daily_alerts.py")]),
)

# How much of a failed step's output goes into the ops message. Enough for the
# exception and its last frames, short enough for one Telegram message.
_OUTPUT_TAIL_CHARS = 1200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_step(name: str, command: list[str], *, timeout: float) -> tuple[bool, str]:
    """Run one step. Returns (ok, output tail already redacted).

    Output is captured and then echoed, so it is both in `docker compose logs` and
    available for the ops message. The trade-off is that a long run's output
    appears when the step ends rather than as it goes; the daily run takes seconds.
    """
    logger.info("step %s: starting (%s)", name, " ".join(command[1:]))
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{timeout:.0f} saniyede bitmedi, süreç öldürüldü (timeout)."
    except OSError as e:
        return False, channels.redact(f"{type(e).__name__}: {e}")

    output = (completed.stdout or "") + (completed.stderr or "")
    # The child's own output may quote a connection string or a webhook; it goes to
    # a log and possibly to Telegram, so it is redacted on both paths.
    output = channels.redact(output)
    if output.strip():
        print(output, end="" if output.endswith("\n") else "\n")

    if completed.returncode == 0:
        logger.info("step %s: ok", name)
        return True, ""

    logger.error("step %s: exit code %s", name, completed.returncode)
    tail = output.strip()[-_OUTPUT_TAIL_CHARS:]
    return False, f"exit code {completed.returncode}\n{tail}".strip()


def run_chain(*, now: datetime | None = None, timeout: float = SCHEDULER_RUN_TIMEOUT_SECONDS) -> bool:
    """The whole daily chain. Records the outcome and alerts on failure.

    Returns True when every step succeeded. Never raises for a step failure: the
    loop has to survive a bad day and try again tomorrow.
    """
    started = now or _now()
    logger.info("daily chain: starting")

    for name, command in STEPS:
        ok, detail = run_step(name, command, timeout=timeout)
        if not ok:
            report_failure(step=name, detail=detail, now=_now())
            return False

    finished = _now()
    scheduling.record_success(SCHEDULER_STATE_PATH, now=finished)
    logger.info(
        "daily chain: ok in %.1fs", (finished - started).total_seconds()
    )
    return True


def report_failure(*, step: str, detail: str, now: datetime) -> None:
    """Record the failed run, then try to tell an operator about it.

    Recording comes first on purpose: the ops channel may be unconfigured or down,
    and the fact that the run failed must survive that. This is the B-04 hook -
    `scheduling.record_failure` writes to the state file today and is where the
    `runs` row (status='failed') belongs once that table exists.
    """
    message = (
        f"GÜNLÜK KOŞU BAŞARISIZ\n"
        f"adım: {step}\n"
        f"zaman: {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"{detail}\n\n"
        f"Müşteriye mesaj GİTMEDİ (zincir && ile bağlı)."
    )
    scheduling.record_failure(SCHEDULER_STATE_PATH, now=now, step=step, message=detail)

    # Loud in the container log whether or not an ops channel exists: with no
    # channel configured this log line and the service's health status are the only
    # things that say the system is broken.
    logger.error("=" * 60)
    for line in message.splitlines():
        logger.error("%s", line)
    logger.error("=" * 60)

    _deliver_ops(message, what="failure")


def check_heartbeat(*, now: datetime | None = None) -> str | None:
    """Alert if no run has succeeded lately. Returns the reason it alerted, or None."""
    now = now or _now()
    state = scheduling.read_state(SCHEDULER_STATE_PATH)
    reason = scheduling.heartbeat_reason(
        now=now,
        last_success_at=scheduling.parse_timestamp(state.get("last_success_at")),
        first_seen_at=scheduling.parse_timestamp(state.get("first_seen_at")),
        last_alert_at=scheduling.parse_timestamp(state.get("last_heartbeat_alert_at")),
        max_hours=SCHEDULER_HEARTBEAT_HOURS,
    )
    if reason is None:
        return None

    logger.error("HEARTBEAT: %s", reason)
    _deliver_ops(f"HEARTBEAT UYARISI\n{reason}", what="heartbeat")
    # Written even if delivery failed: the point of the stamp is to stop one alert
    # per restart, and a channel that is down produces exactly that restart loop.
    scheduling.record_heartbeat_alert(SCHEDULER_STATE_PATH, now=now)
    return reason


def _deliver_ops(message: str, *, what: str) -> None:
    if not ops_channels_configured():
        logger.error(
            "ops alert (%s) NOT DELIVERED: no operator channel configured. "
            "Set OPS_TELEGRAM_CHAT_ID or OPS_ALERT_WEBHOOK_URL (see .env.example); "
            "until then this log line and `docker compose ps` are the only signal.",
            what,
        )
        return
    for channel, result in send_ops_alert(message).items():
        logger.info("ops alert (%s) -> %s: %s", what, channel, result)


def healthcheck(*, now: datetime | None = None) -> int:
    """0 = the scheduler is doing its job; 1 = it is not. Used by compose.

    This is the part that makes a failure visible with no ops channel at all:
    `docker compose ps` prints (unhealthy) next to the service, and that survives
    a log nobody was watching.
    """
    now = now or _now()
    state = scheduling.read_state(SCHEDULER_STATE_PATH)
    success = scheduling.parse_timestamp(state.get("last_success_at"))
    failure = scheduling.parse_timestamp(state.get("last_failure_at"))

    if failure is not None and (success is None or failure > success):
        print(f"unhealthy: son koşu başarısız ({state.get('last_failure_step')})")
        return 1

    reason = scheduling.heartbeat_reason(
        now=now,
        last_success_at=success,
        first_seen_at=scheduling.parse_timestamp(state.get("first_seen_at")),
        # The health status must not be silenced by an alert having been sent: that
        # stamp exists to spare the ops channel, not to make the container look well.
        last_alert_at=None,
        max_hours=SCHEDULER_HEARTBEAT_HOURS,
    )
    if reason is not None:
        print(f"unhealthy: {reason}")
        return 1

    if success is None:
        # Freshly deployed: no run has been due yet, which heartbeat_reason already
        # decided is fine. Healthy, but say why so nobody reads it as a green run.
        print("ok: henüz koşu zamanı gelmedi (yeni kurulum)")
        return 0
    print(f"ok: son başarılı koşu {success.isoformat()}")
    return 0


def _schedule_from_config() -> tuple:
    """(run_at, tz, run_days), or ScheduleConfigError with a message worth reading."""
    return (
        scheduling.parse_run_at(RUN_AT),
        scheduling.resolve_timezone(SCHEDULER_TIMEZONE),
        scheduling.parse_run_days(RUN_DAYS),
    )


def loop(*, sleep=time_module.sleep, max_cycles: int | None = None) -> int:
    """Sleep until the next due time, run the chain, check the heartbeat, repeat.

    `sleep` and `max_cycles` are injected so the loop itself can be tested without
    a real clock.
    """
    run_at, tz, run_days = _schedule_from_config()
    logger.info(
        "scheduler: %s | heartbeat %.0f saat | durum dosyası %s",
        scheduling.describe_schedule(run_at=run_at, tz=tz, run_days=run_days),
        SCHEDULER_HEARTBEAT_HOURS,
        SCHEDULER_STATE_PATH,
    )
    if not ops_channels_configured():
        logger.warning(
            "scheduler: no operator channel configured - a failed run will be "
            "reported in this log and in the container's health status only"
        )

    scheduling.note_started(SCHEDULER_STATE_PATH, now=_now())
    # Checked before the first sleep too: a container that was down for two days
    # should say so at startup, not at 09:00 tomorrow.
    check_heartbeat()

    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        now = _now()
        target = scheduling.next_run_at(now, run_at=run_at, tz=tz, run_days=run_days)
        wait = scheduling.seconds_until(now, target)
        logger.info(
            "scheduler: sonraki koşu %s (%.1f saat sonra)",
            target.isoformat(timespec="minutes"),
            wait / 3600,
        )
        sleep(wait)
        run_chain()
        check_heartbeat()
        cycles += 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run the chain now and exit")
    parser.add_argument(
        "--next",
        type=int,
        metavar="N",
        help="print the next N due times and exit (runs nothing)",
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="exit 1 if the last run failed or no run succeeded within the heartbeat window",
    )
    args = parser.parse_args(argv)

    configure_logging()

    if args.healthcheck:
        return healthcheck()

    if args.next:
        run_at, tz, run_days = _schedule_from_config()
        print(scheduling.describe_schedule(run_at=run_at, tz=tz, run_days=run_days))
        cursor = _now()
        for _ in range(args.next):
            cursor = scheduling.next_run_at(cursor, run_at=run_at, tz=tz, run_days=run_days)
            print(f"  {cursor.isoformat(timespec='minutes')}  ({cursor.astimezone(timezone.utc):%H:%M} UTC)")
        return 0

    if args.once:
        ok = run_chain()
        check_heartbeat()
        return 0 if ok else 1

    return loop()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except scheduling.ScheduleConfigError as e:
        # A misconfigured schedule is fatal and must stay fatal: the container exits
        # non-zero, compose restarts it, it exits again, and `docker compose ps`
        # shows a service that will not stay up. That is the intended signal - far
        # better than a container that is "running" and silently never fires.
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2) from None
