"""A credential must never reach the terminal, the log or a traceback.

The Telegram Bot API puts the bot token in the URL path, and a webhook URL *is*
the credential. So any exception that quotes the URL quotes the secret - and
urllib does exactly that for InvalidURL, which is what a token with a stray
newline produces. These tests pin the three defences:

  1. config strips whitespace from credentials at load time,
  2. send_telegram rejects a malformed token before it becomes a URL,
  3. every error leaving the channel layer is redacted, with the original
     exception *not* chained (a chained cause would print the raw message in
     any traceback).
"""
import http.client
import logging
import urllib.error

import pytest

from src.notifications import channels, notify

REAL_LOOKING_TOKEN = "8123456789:AAFakeBotTokenFromDotEnv_abcdefghijk"


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


# --- 1. config -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"{REAL_LOOKING_TOKEN}\n", REAL_LOOKING_TOKEN),     # secret file with trailing newline
        (f"  {REAL_LOOKING_TOKEN}  ", REAL_LOOKING_TOKEN),  # dotenv keeps spaces inside quotes
        ("   ", None),
        ("", None),
    ],
)
def test_env_secret_strips_whitespace(monkeypatch, raw, expected):
    import config

    monkeypatch.setenv("SOME_SECRET", raw)
    assert config._env_secret("SOME_SECRET") == expected


def test_env_secret_unset_is_none(monkeypatch):
    import config

    monkeypatch.delenv("SOME_SECRET", raising=False)
    assert config._env_secret("SOME_SECRET") is None


# --- 2. token shape checked before it becomes a URL ----------------------------
@pytest.mark.parametrize(
    "bad_token",
    [f"{REAL_LOOKING_TOKEN}\n", f"{REAL_LOOKING_TOKEN} ", "not-a-token", "8123456789"],
)
def test_malformed_token_is_rejected_without_echoing_it(monkeypatch, bad_token):
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", bad_token)
    monkeypatch.setattr(channels, "TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setattr(
        channels.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("a malformed token must never reach the network"),
    )

    with pytest.raises(channels.NotConfigured) as caught:
        channels.send_telegram("merhaba")

    assert bad_token.strip() not in str(caught.value)
    assert "Value not shown" in str(caught.value)


# --- 3. redaction of anything that escapes the channel layer -------------------
def test_invalid_url_error_is_redacted_and_not_chained(monkeypatch):
    """The exact failure seen in practice: InvalidURL quoting the whole path."""
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", REAL_LOOKING_TOKEN)

    def fake_urlopen(request, timeout=None):
        raise http.client.InvalidURL(
            f"URL can't contain control characters. '/bot{REAL_LOOKING_TOKEN}\\n/sendMessage'"
        )

    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as caught:
        channels._post_json(f"https://api.telegram.org/bot{REAL_LOOKING_TOKEN}/sendMessage", {})

    message = str(caught.value)
    assert REAL_LOOKING_TOKEN not in message
    assert channels.REDACTED in message
    assert "InvalidURL" in message                       # still diagnosable
    # A chained cause would put the raw message back into any printed traceback.
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_network_error_quoting_the_url_is_redacted(monkeypatch):
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", REAL_LOOKING_TOKEN)

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(f"timed out calling {request.full_url}")

    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as caught:
        channels._post_json(f"https://api.telegram.org/bot{REAL_LOOKING_TOKEN}/sendMessage", {})

    assert REAL_LOOKING_TOKEN not in str(caught.value)


def test_webhook_url_is_treated_as_a_secret(monkeypatch):
    hook = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXXXXXXXXX"
    monkeypatch.setattr(channels, "ALERT_WEBHOOK_URL", hook)

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(f"connection reset by {request.full_url}")

    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as caught:
        channels.send_webhook("merhaba")

    assert hook not in str(caught.value)


def test_redact_catches_a_token_it_was_not_told_about():
    """Pattern layer: a token-shaped string is removed even if it is not the configured one."""
    text = f"failed for bot{REAL_LOOKING_TOKEN} and /bot{REAL_LOOKING_TOKEN}/getMe"
    cleaned = channels.redact(text)
    assert REAL_LOOKING_TOKEN not in cleaned


def test_notify_result_and_log_never_contain_the_token(monkeypatch, caplog):
    """End to end through the dispatcher: what is printed and what is logged."""
    import pandas as pd

    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", REAL_LOOKING_TOKEN)
    monkeypatch.setattr(channels, "TELEGRAM_CHAT_ID", "-100")

    def fake_urlopen(request, timeout=None):
        raise http.client.InvalidURL(f"bad url '{request.full_url}'")

    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    alerts = pd.DataFrame(
        {
            "student_id": ["S1"],
            "churn_probability": [0.61],
            "status": ["new"],
            "top_reasons": ["days_since_last_contact (+0.50)"],
        }
    )
    with caplog.at_level(logging.DEBUG):
        results = notify.send_notifications(alerts, enabled=["telegram"])

    assert results["telegram"].startswith("error:")
    assert REAL_LOOKING_TOKEN not in results["telegram"]
    assert REAL_LOOKING_TOKEN not in caplog.text


def test_telegram_setup_call_never_raises_or_leaks(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "telegram_setup.py"
    spec = importlib.util.spec_from_file_location("telegram_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "TELEGRAM_BOT_TOKEN", REAL_LOOKING_TOKEN)
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", REAL_LOOKING_TOKEN)

    def fake_urlopen(request, timeout=None):
        raise http.client.InvalidURL(f"bad url '{request.full_url}'")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.call("getMe")

    assert result["ok"] is False
    assert REAL_LOOKING_TOKEN not in result["description"]
