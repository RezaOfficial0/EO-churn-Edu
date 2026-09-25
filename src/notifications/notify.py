"""Send one run's alert to every configured channel.

One channel failing must not stop the others, and must not fail the daily run:
the pipeline's job is scoring and recording, and those are already done by the
time this is called. A failed Telegram post is reported, not raised.
"""
import logging
from datetime import datetime

import pandas as pd

from config import NOTIFY_CHANNELS
from src.notifications import channels
from src.notifications.message import build_html, build_message

logger = logging.getLogger(__name__)

CHANNELS = ("telegram", "email", "webhook")


def send_notifications(
    new_alerts: pd.DataFrame,
    *,
    still_at_risk: pd.DataFrame | None = None,
    students: pd.DataFrame | None = None,
    previous_probabilities: dict[str, float] | None = None,
    run_at: datetime | None = None,
    quarantine=None,
    enabled: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """Build the message and deliver it. Returns {channel: "sent" | "error: ..."}.

    `enabled` overrides config.NOTIFY_CHANNELS (the CLI's --channels). An empty
    list means "print only", which is the default on a developer machine so a
    test run never messages a real person.

    `quarantine` is passed straight through to the message builders (B-28); it adds
    a line about rows this run could not read, and only when that share is worth
    reporting.
    """
    enabled = list(NOTIFY_CHANNELS if enabled is None else enabled)
    unknown = [channel for channel in enabled if channel not in CHANNELS]
    if unknown:
        raise ValueError(f"unknown notification channel(s): {unknown}. known: {list(CHANNELS)}")

    subject, text = build_message(
        new_alerts,
        still_at_risk=still_at_risk,
        students=students,
        previous_probabilities=previous_probabilities,
        run_at=run_at,
        quarantine=quarantine,
    )
    results: dict[str, str] = {}

    for channel in enabled:
        if dry_run:
            results[channel] = "dry-run (gönderilmedi)"
            continue
        try:
            if channel == "telegram":
                channels.send_telegram(text)
            elif channel == "email":
                html = build_html(
                    new_alerts,
                    still_at_risk=still_at_risk,
                    students=students,
                    previous_probabilities=previous_probabilities,
                    run_at=run_at,
                    quarantine=quarantine,
                )
                channels.send_email(subject, text, html)
            elif channel == "webhook":
                channels.send_webhook(text)
            results[channel] = "sent"
        except channels.NotConfigured as e:
            # Second line of defence: channels that do not go through _post_json
            # (email) and future ones must not leak a secret into the log either.
            message = channels.redact(str(e))
            results[channel] = f"not configured: {message}"
            logger.warning("%s: not configured - %s", channel, message)
        except Exception as e:  # noqa: BLE001 - a channel must never fail the run
            message = channels.redact(f"{type(e).__name__}: {e}")
            results[channel] = f"error: {message}"
            logger.error("%s: delivery failed - %s", channel, message)

    return results
