"""The delivery channels: Telegram, email (SMTP), and a generic webhook.

Each `send_*` function does one thing and raises on failure. Deciding which
channels to use, and surviving one of them failing, is `notify.py`'s job.

No new dependencies: Telegram and the webhook go through urllib, email through
the standard library's smtplib.
"""
import json
import logging
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from config import (
    ALERT_WEBHOOK_URL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_STARTTLS,
    SMTP_TO,
    SMTP_USER,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
# Telegram rejects anything longer; truncate rather than fail to deliver.
TELEGRAM_MAX_CHARS = 4096


class NotConfigured(Exception):
    """The channel was asked for but its settings are missing."""


def _post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as e:
        # The body carries the actual reason (bad token, wrong chat id, ...) and
        # is far more useful than "HTTP Error 400: Bad Request".
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


# --- Telegram ---------------------------------------------------------------
def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise NotConfigured(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set (see .env.example)"
        )
    if len(text) > TELEGRAM_MAX_CHARS:
        text = text[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n... (kısaltıldı)"

    _post_json(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        # No parse_mode: the message is plain text, and student ids or feature
        # labels containing _ or * would break Markdown parsing.
        {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
    )
    logger.info("telegram: message sent to chat %s", TELEGRAM_CHAT_ID)


# --- Email ------------------------------------------------------------------
def send_email(subject: str, text: str, html: str | None = None) -> None:
    if not SMTP_HOST or not SMTP_TO:
        raise NotConfigured("SMTP_HOST and SMTP_TO must be set (see .env.example)")
    if not SMTP_FROM:
        raise NotConfigured("SMTP_FROM (or SMTP_USER) must be set")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = ", ".join(SMTP_TO)
    message.set_content(text)
    if html:
        # Clients that render HTML use it; the rest fall back to the plain part.
        message.add_alternative(html, subtype="html")

    if SMTP_STARTTLS:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD or "")
            server.send_message(message)
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD or "")
            server.send_message(message)

    logger.info("email: message sent to %s", ", ".join(SMTP_TO))


# --- Webhook (Slack / Discord / anything taking {"text": ...}) --------------
def send_webhook(text: str) -> None:
    if not ALERT_WEBHOOK_URL:
        raise NotConfigured("ALERT_WEBHOOK_URL must be set (see .env.example)")
    _post_json(ALERT_WEBHOOK_URL, {"text": text})
    logger.info("webhook: message posted")
