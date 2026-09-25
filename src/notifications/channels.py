"""The delivery channels: Telegram, email (SMTP), and a generic webhook.

Each `send_*` function does one thing and raises on failure. Deciding which
channels to use, and surviving one of them failing, is `notify.py`'s job.

No new dependencies: Telegram and the webhook go through urllib, email through
the standard library's smtplib.
"""
import json
import logging
import re
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from config import (
    API_KEY,
    ALERT_WEBHOOK_URL,
    DATABASE_URL,
    OPS_ALERT_WEBHOOK_URL,
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

# Telegram bot token format (digits:secret). Checked before the token goes
# into the request URL: a malformed token would otherwise leak via the error message.
TELEGRAM_TOKEN_PATTERN = re.compile(r"\d+:[A-Za-z0-9_-]+")

# Replaces a secret wherever redact() finds one.
REDACTED = "***"
# The token as it appears inside a Telegram URL: /bot<anything up to the next /, space or quote>.
_BOT_PATH = re.compile(r"/bot[^/\s'\"]+")
# A bare Telegram-token-shaped string. Stricter than TELEGRAM_TOKEN_PATTERN so ordinary
# text such as "12:30" is left alone - over-redacting would hide useful error details.
_TOKEN_SHAPED = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")


def redact(text: str) -> str:
    """<tek cümle: ne yapar, neden var>"""
    for secret in (
        TELEGRAM_BOT_TOKEN,
        SMTP_PASSWORD,
        API_KEY,
        ALERT_WEBHOOK_URL,
        # The ops webhook (B-14) is a credential exactly like the customer one, and
        # it appears in the errors of the alert that reports a failed run - the one
        # error path most likely to be pasted into a chat by whoever is debugging.
        OPS_ALERT_WEBHOOK_URL,
        # The connection string carries the database password. SQLAlchemy masks it in
        # its own messages, but the scheduler now forwards a failed run's raw output
        # to an operator channel (B-14), and that output is whatever the child
        # printed - a psycopg2 error, a traceback, or a print() somebody added.
        DATABASE_URL,
    ):
        if secret == None or len(secret) < 8:
            continue
        text = text.replace(secret, REDACTED)
    text = _BOT_PATH.sub("/bot" + REDACTED, text)
    text = _TOKEN_SHAPED.sub(REDACTED, text)
    return text

class NotConfigured(Exception):
    """The channel was asked for but its settings are missing."""


def _post_json(url: str, payload: dict) -> None:

    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as e:
        # The body carries the actual reason (bad token, wrong chat id, ...) and
        # is far more useful than "HTTP Error 400: Bad Request".
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(redact(f"HTTP {e.code}: {detail}")) from None
    except Exception as e:
        raise RuntimeError(redact(f"{type(e).__name__}: {e}")) from None




# --- Telegram ---------------------------------------------------------------
def send_telegram(text: str, *, chat_id: str | None = None) -> None:
    """Post to TELEGRAM_CHAT_ID, or to `chat_id` when one is given.

    The override exists for the operator channel (B-14): a failed run has to reach
    US, not the customer's group, and it is the same bot token either way - only
    the destination differs. Defaulting to the customer chat keeps every existing
    caller unchanged.
    """
    chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not chat:
        raise NotConfigured(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set (see .env.example)"
        )
    if not TELEGRAM_TOKEN_PATTERN.fullmatch(TELEGRAM_BOT_TOKEN):
        raise NotConfigured(
            "TELEGRAM_BOT_TOKEN is malformed (expected digits:letters, e.g. 123456:ABC-def). "
            "Copy it again from @BotFather. Value not shown."
        )
    if len(text) > TELEGRAM_MAX_CHARS:
        text = text[: TELEGRAM_MAX_CHARS - 20].rstrip() + "\n... (kısaltıldı)"

    _post_json(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        # No parse_mode: the message is plain text, and student ids or feature
        # labels containing _ or * would break Markdown parsing.
        {"chat_id": chat, "text": text, "disable_web_page_preview": True},
    )
    logger.info("telegram: message sent to chat %s", chat)


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
def send_webhook(text: str, *, url: str | None = None) -> None:
    """Post to ALERT_WEBHOOK_URL, or to `url` when one is given (the ops hook, B-14)."""
    target = url or ALERT_WEBHOOK_URL
    if not target:
        raise NotConfigured("ALERT_WEBHOOK_URL must be set (see .env.example)")
    _post_json(target, {"text": text})
    logger.info("webhook: message posted")
