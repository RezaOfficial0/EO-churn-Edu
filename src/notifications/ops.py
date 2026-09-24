"""The operator channel: where a FAILED run is reported (B-14).

Separate from everything in `notify.py` by design. That module delivers the daily
risk message to the customer's mentors; this one delivers "the 09:00 run died" to
us. Sharing one destination would put stack-trace-shaped messages in the group a
mentor reads every morning, and would tell the customer about every outage first.

The channels themselves are reused as they are (`channels.send_telegram` /
`channels.send_webhook`), with the destination overridden per call:

  OPS_TELEGRAM_CHAT_ID   - same TELEGRAM_BOT_TOKEN, our chat instead of theirs
  OPS_ALERT_WEBHOOK_URL  - our Slack/Discord hook

Neither is required. With neither set, `send_ops_alert` reports that it delivered
nothing and the caller still logs the failure loudly - silence must never be
mistaken for success.
"""
import logging

from config import (
    OPS_ALERT_WEBHOOK_URL,
    OPS_TELEGRAM_CHAT_ID,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from src.notifications import channels

logger = logging.getLogger(__name__)

# Prefix on every ops message, so one glance separates it from a customer alert
# if the two ever land in the same client.
OPS_PREFIX = "[EO-Churn OPS]"


def ops_channels_configured() -> list[str]:
    """Which operator channels are usable right now, in delivery order."""
    configured = []
    if OPS_TELEGRAM_CHAT_ID and TELEGRAM_BOT_TOKEN:
        configured.append("telegram")
    if OPS_ALERT_WEBHOOK_URL:
        configured.append("webhook")
    return configured


def send_ops_alert(text: str) -> dict[str, str]:
    """Deliver one operator message. Returns {channel: "sent" | "error: ..."}.

    Never raises. It is called from the failure path, and an exception here would
    replace a reported failure with an unreported one.
    """
    message = f"{OPS_PREFIX} {text}"
    results: dict[str, str] = {}

    if OPS_TELEGRAM_CHAT_ID and not TELEGRAM_BOT_TOKEN:
        logger.warning(
            "ops: OPS_TELEGRAM_CHAT_ID is set but TELEGRAM_BOT_TOKEN is not - "
            "the ops channel reuses the same bot, so no Telegram alert can be sent"
        )
    if OPS_TELEGRAM_CHAT_ID and OPS_TELEGRAM_CHAT_ID == TELEGRAM_CHAT_ID:
        # Allowed (a one-person team may legitimately want both in one place), but
        # it defeats the separation the whole module exists for, so say so once.
        logger.warning(
            "ops: OPS_TELEGRAM_CHAT_ID is the same chat as TELEGRAM_CHAT_ID - "
            "failure messages will land in the customer's group"
        )

    for channel in ops_channels_configured():
        try:
            if channel == "telegram":
                channels.send_telegram(message, chat_id=OPS_TELEGRAM_CHAT_ID)
            else:
                channels.send_webhook(message, url=OPS_ALERT_WEBHOOK_URL)
            results[channel] = "sent"
        except Exception as e:  # noqa: BLE001 - reporting a failure must not fail
            # redact() covers the ops webhook too (it is a credential in itself),
            # so the reason can be logged without leaking the destination.
            detail = channels.redact(f"{type(e).__name__}: {e}")
            results[channel] = f"error: {detail}"
            logger.error("ops %s: delivery failed - %s", channel, detail)

    return results
