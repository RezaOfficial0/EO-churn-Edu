"""Send the latest daily run's alert to Telegram, email, and/or a webhook.

Run it right after the daily pipeline - this is the step that reaches a person:

    python -m pipeline.daily_pipeline
    python scripts/send_daily_alerts.py

    python scripts/send_daily_alerts.py --dry-run              # show it, send nothing
    python scripts/send_daily_alerts.py --channels telegram    # just this one

Which channels are used comes from `NOTIFY_CHANNELS` in .env (see .env.example).
With none set, the message is only printed - so running this on a laptop while
developing never messages a real mentor.

It reads the alert log, not the model: the run must already have happened. The
message details the students flagged for the FIRST time in that run (`status`
= "new") and summarises the ones that were already flagged, so a daily message
stays actionable instead of repeating the same names every morning.

Two refusals, both deliberate. If the alert log has no runs at all, or its most
recent run is older than --max-age-hours, nothing is sent and the exit code is 1.
"Bugün risk altında öğrenci yok" and "the pipeline never ran" are very different
facts, and a scheduler that fails silently at 09:00 must not send a reassuring
message built from stale data. Use --force to send anyway.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import NOTIFY_CHANNELS, RUN_QUALITY_PATH, STUDENT_INFO
from src.data.features import add_monthly_value
from src.data.loader import (
    latest_run_alerts,
    load_daily_students,
    previous_run_probabilities,
)
from src.data.run_quality import read_report
from src.logging_setup import configure_logging
from src.notifications.message import build_message
from src.notifications.notify import CHANNELS, send_notifications

_ID_COLUMN = STUDENT_INFO[0]


def split_latest_run() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(new, still_at_risk) rows of the most recent recorded run, riskiest first."""
    last_run = latest_run_alerts()
    if last_run.empty:
        return last_run, last_run
    last_run = last_run.sort_values("churn_probability", ascending=False)
    return (
        last_run[last_run["status"] == "new"],
        last_run[last_run["status"] == "still_at_risk"],
    )


def run_age(alerts: pd.DataFrame) -> timedelta | None:
    """How long ago the run in `alerts` happened, or None if it cannot be told."""
    if alerts.empty or "run_at" not in alerts.columns:
        return None
    try:
        run_at = pd.to_datetime(alerts["run_at"], format="mixed", utc=True).max()
    except Exception:  # noqa: BLE001 - an unparseable timestamp is not worth failing over
        return None
    if pd.isna(run_at):
        return None
    return datetime.now(timezone.utc) - run_at.to_pydatetime()


def todays_students() -> pd.DataFrame | None:
    """Today's student rows, used to put a real value next to each SHAP reason.

    Optional on purpose: if the daily data is unreachable the message still goes
    out, with "veri yok" where a number would be.
    """
    try:
        # Same derivation the model saw, so a derived feature in the reasons shows
        # its value instead of "veri yok".
        return add_monthly_value(load_daily_students())
    except Exception as e:  # noqa: BLE001
        print(f"uyarı: öğrenci verisi okunamadı, değerler gösterilmeyecek ({e})", file=sys.stderr)
        return None


def earlier_probabilities() -> dict[str, float]:
    """The previous run's probabilities, used to show which way a repeat moved.

    Optional like todays_students(): an unreadable alert log here costs the arrow
    next to a repeat student, not the message.
    """
    try:
        return previous_run_probabilities()
    except Exception as e:  # noqa: BLE001
        print(f"uyarı: önceki koşu okunamadı, değişim yönü gösterilmeyecek ({e})", file=sys.stderr)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--channels",
        help=f"comma-separated, overrides NOTIFY_CHANNELS. Available: {', '.join(CHANNELS)}",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the message, send nothing"
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="refuse to send if the last recorded run is older than this (default: 24)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="send even if there is no run, or the last one is stale",
    )
    args = parser.parse_args()

    configure_logging()

    new_alerts, still_at_risk = split_latest_run()

    # Guard 1: no run at all. Without this, an empty alert log produces a cheerful
    # "no students at risk today" - which is exactly the wrong message when the
    # truth is that the pipeline never ran.
    if new_alerts.empty and still_at_risk.empty and not args.force:
        print(
            "error: alert log'da kayıtlı koşu yok, bildirim gönderilmedi.\n"
            "Önce günlük koşuyu çalıştır:  python -m pipeline.daily_pipeline\n"
            "(gerçekten boş bir uyarı göndermek istiyorsan: --force)",
            file=sys.stderr,
        )
        return 1

    # Guard 2: the last run is old. A scheduler that failed at 09:00 must not have
    # yesterday's alerts re-sent as if they were today's.
    age = run_age(pd.concat([new_alerts, still_at_risk]))
    if age is not None and age > timedelta(hours=args.max_age_hours) and not args.force:
        hours = age.total_seconds() / 3600
        print(
            f"error: son koşu {hours:.0f} saat önce yapılmış "
            f"(sınır: {args.max_age_hours:.0f} saat), bildirim gönderilmedi.\n"
            "Günlük koşu bugün çalışmamış olabilir:  python -m pipeline.daily_pipeline\n"
            "(eski koşuyu yine de göndermek istiyorsan: --force)",
            file=sys.stderr,
        )
        return 1

    students = todays_students()
    previous = earlier_probabilities()
    # What the run that produced these alerts had to skip (B-28). None when the
    # pipeline is older than this file or the state directory is not writable, in
    # which case the message is exactly what it was before - one line short, not
    # wrong.
    quarantine = read_report(RUN_QUALITY_PATH)

    enabled = None
    if args.channels:
        enabled = [channel.strip().lower() for channel in args.channels.split(",") if channel.strip()]

    subject, text = build_message(
        new_alerts,
        still_at_risk=still_at_risk,
        students=students,
        previous_probabilities=previous,
        quarantine=quarantine,
    )
    print(text)
    print()

    try:
        results = send_notifications(
            new_alerts,
            still_at_risk=still_at_risk,
            students=students,
            previous_probabilities=previous,
            quarantine=quarantine,
            enabled=enabled,
            dry_run=args.dry_run,
        )
    except ValueError as e:  # unknown channel name
        print(f"error: {e}", file=sys.stderr)
        return 1

    if not results:
        configured = enabled if enabled is not None else NOTIFY_CHANNELS
        if not configured:
            print("(hiçbir kanal açık değil - NOTIFY_CHANNELS boş, sadece ekrana yazıldı)")
        return 0

    print("Kanallar:")
    for channel, result in results.items():
        print(f"  {channel:<9} {result}")

    failed = [c for c, r in results.items() if r.startswith(("error", "not configured"))]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
