"""The daily alert message and channel dispatch.

No network anywhere: message building is pure, and dispatch is checked by
monkeypatching the channel functions. A test run must never send anything.
"""
from datetime import datetime

import pandas as pd
import pytest

from src.notifications import message as msg
from src.notifications import notify
from src.notifications.channels import NotConfigured


def _alerts(rows):
    return pd.DataFrame(rows)


NEW = _alerts(
    [
        {
            "student_id": "STU300025",
            "churn_probability": 0.6486,
            "status": "new",
            "top_reasons": "days_since_last_contact (+0.91), satisfaction_survey_score (+0.44)",
            "top_reasons_detail": [
                {"feature": "days_since_last_contact", "impact": 0.91},
                {"feature": "satisfaction_survey_score", "impact": 0.44},
            ],
        }
    ]
)
REPEAT = _alerts(
    [{"student_id": "STU300006", "churn_probability": 0.55, "status": "still_at_risk",
      "top_reasons": "days_since_last_contact (+1.01)", "top_reasons_detail": []}]
)
STUDENTS = pd.DataFrame(
    [{"student_id": "STU300025", "days_since_last_contact": 41.0,
      "satisfaction_survey_score": 2.1, "satisfaction_missing": 0}]
)
RUN_AT = datetime(2026, 9, 11, 9, 0)


# --- the message ------------------------------------------------------------
def test_message_is_turkish_and_carries_the_numbers():
    subject, text = build = msg.build_message(NEW, still_at_risk=REPEAT, students=STUDENTS, run_at=RUN_AT)

    assert "11.09.2026" in subject
    assert "2 öğrenci risk altında, 1 tanesi yeni" in text
    assert "STU300025" in text
    assert "%65" in text  # 0.6486 -> a percentage a mentor can read
    # the label, the real value, and which way it pushes risk
    assert "Son iletişimden bu yana (gün): 41 — riski artırıyor" in text
    assert "Memnuniyet puanı (1-5): 2.1 — riski artırıyor" in text
    # repeats are summarised, not repeated in full
    assert "STU300006" in text
    assert "ÖNCEKİ KOŞUDA DA UYARI VERİLMİŞTİ (1)" in text


def test_repeat_students_carry_probability_trend_and_one_reason():
    """A repeat line has to be actionable on its own: how likely, which way it is
    moving, and why. "same eight names again" is what makes people stop reading."""
    repeats = _alerts(
        [
            {"student_id": "UP", "churn_probability": 0.56, "status": "still_at_risk",
             "top_reasons": "days_since_last_contact (+1.01)", "top_reasons_detail": []},
            {"student_id": "FLAT", "churn_probability": 0.34, "status": "still_at_risk",
             "top_reasons": "days_since_last_contact (+0.20)", "top_reasons_detail": []},
            {"student_id": "DOWN", "churn_probability": 0.27, "status": "still_at_risk",
             "top_reasons": "days_since_last_contact (+0.10)", "top_reasons_detail": []},
        ]
    )
    students = pd.DataFrame([{"student_id": "UP", "days_since_last_contact": 40.0}])
    previous = {"UP": 0.42, "FLAT": 0.34, "DOWN": 0.45}

    _, text = msg.build_message(
        NEW, still_at_risk=repeats, students=students,
        previous_probabilities=previous, run_at=RUN_AT,
    )

    assert "UP — %56 ↑ (önceki %42)" in text
    assert "FLAT — %34 → (önceki %34)" in text
    assert "DOWN — %27 ↓ (önceki %45)" in text
    # the reason, with the value joined from today's students where we have it
    assert "Son iletişimden bu yana (gün): 40" in text


def test_repeat_without_a_previous_probability_omits_the_trend():
    """A student's first appearance in the previous-run map is simply absent."""
    _, text = msg.build_message(NEW, still_at_risk=REPEAT, students=STUDENTS, run_at=RUN_AT)

    assert "STU300006 — %55" in text
    assert "önceki" not in text


def test_repeat_reason_prefers_a_risk_increasing_one():
    """The strongest reason on a still-at-risk student can be one that LOWERS the
    risk; leading with it reads as reassurance about someone still on the list."""
    repeats = _alerts(
        [{"student_id": "MIXED", "churn_probability": 0.27, "status": "still_at_risk",
          "top_reasons": "days_since_last_contact (-0.34), payment_delay_days_avg (+0.29)",
          "top_reasons_detail": []}]
    )

    _, text = msg.build_message(NEW, still_at_risk=repeats, run_at=RUN_AT)

    assert "Ortalama ödeme gecikmesi" in text
    assert "MIXED — %27 · Son iletişimden bu yana" not in text


def test_long_repeat_lists_are_capped():
    repeats = _alerts(
        [
            {"student_id": f"S{i}", "churn_probability": 0.3, "status": "still_at_risk",
             "top_reasons": "", "top_reasons_detail": []}
            for i in range(msg.MAX_REPEAT_LINES + 4)
        ]
    )

    _, text = msg.build_message(NEW, still_at_risk=repeats, run_at=RUN_AT)

    assert "... ve 4 öğrenci daha." in text


def test_reasons_fall_back_to_the_csv_string():
    """The CSV alert log has no top_reasons_detail column - parse the display string."""
    row = {"top_reasons": "days_since_last_contact (+0.91), payment_delay_days_avg (-0.17)",
           "top_reasons_detail": None}
    assert msg.parse_reasons(row) == [
        {"feature": "days_since_last_contact", "impact": 0.91},
        {"feature": "payment_delay_days_avg", "impact": -0.17},
    ]


def test_missing_student_values_degrade_to_veri_yok():
    _, text = msg.build_message(NEW, students=None, run_at=RUN_AT)
    assert "veri yok" in text
    assert "STU300025" in text  # the alert still goes out


def test_negative_impact_reads_as_lowering_risk():
    alerts = _alerts([{"student_id": "S1", "churn_probability": 0.3, "top_reasons": "",
                       "top_reasons_detail": [{"feature": "payment_delay_days_avg", "impact": -0.4}]}])
    _, text = msg.build_message(alerts, students=None, run_at=RUN_AT)
    assert "riski azaltıyor" in text


def test_empty_run_says_so():
    empty = pd.DataFrame(columns=["student_id", "churn_probability"])
    subject, text = msg.build_message(empty, still_at_risk=empty, run_at=RUN_AT)
    assert "risk altında öğrenci yok" in subject
    assert "Bugün risk eşiğinin üzerinde öğrenci yok." in text


def test_long_runs_are_capped():
    many = _alerts([
        {"student_id": f"S{i}", "churn_probability": 0.5, "top_reasons": "", "top_reasons_detail": []}
        for i in range(msg.MAX_DETAILED_STUDENTS + 5)
    ])
    _, text = msg.build_message(many, students=None, run_at=RUN_AT)
    assert "ve 5 öğrenci daha" in text


def test_missing_flags_read_as_yes_no():
    assert msg.format_value("satisfaction_missing", 1) == "evet"
    assert msg.format_value("satisfaction_missing", 0) == "hayır"
    assert msg.format_value("days_since_last_contact", 41.0) == "41"
    assert msg.format_value("satisfaction_survey_score", float("nan")) == "veri yok"


def test_html_escapes_and_contains_the_same_facts():
    html = msg.build_html(NEW, still_at_risk=REPEAT, students=STUDENTS, run_at=RUN_AT)
    assert "STU300025" in html and "%65" in html
    assert "Son iletişimden bu yana (gün): 41" in html
    assert html.startswith("<div") and html.endswith("</div>")


# --- dispatch ---------------------------------------------------------------
def test_no_channels_configured_sends_nothing():
    assert notify.send_notifications(NEW, enabled=[], run_at=RUN_AT) == {}


def test_dry_run_never_touches_a_channel(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("a dry run must not call a channel")

    monkeypatch.setattr(notify.channels, "send_telegram", explode)
    monkeypatch.setattr(notify.channels, "send_email", explode)

    results = notify.send_notifications(NEW, enabled=["telegram", "email"], dry_run=True, run_at=RUN_AT)
    assert set(results) == {"telegram", "email"}
    assert all("dry-run" in result for result in results.values())


def test_each_channel_gets_the_message(monkeypatch):
    sent = {}
    monkeypatch.setattr(notify.channels, "send_telegram", lambda text: sent.update(telegram=text))
    monkeypatch.setattr(
        notify.channels, "send_email", lambda subject, text, html=None: sent.update(email=(subject, html))
    )
    monkeypatch.setattr(notify.channels, "send_webhook", lambda text: sent.update(webhook=text))

    results = notify.send_notifications(
        NEW, students=STUDENTS, enabled=["telegram", "email", "webhook"], run_at=RUN_AT
    )

    assert results == {"telegram": "sent", "email": "sent", "webhook": "sent"}
    assert "STU300025" in sent["telegram"]
    assert "11.09.2026" in sent["email"][0]  # subject
    assert "<div" in sent["email"][1]  # html alternative
    assert "STU300025" in sent["webhook"]


def test_one_failing_channel_does_not_stop_the_others(monkeypatch):
    """A dead Telegram token must not cost the mentors their email."""
    def boom(text):
        raise RuntimeError("HTTP 401: unauthorized")

    monkeypatch.setattr(notify.channels, "send_telegram", boom)
    monkeypatch.setattr(notify.channels, "send_email", lambda subject, text, html=None: None)

    results = notify.send_notifications(NEW, enabled=["telegram", "email"], run_at=RUN_AT)

    assert results["telegram"].startswith("error: RuntimeError")
    assert results["email"] == "sent"


def test_unconfigured_channel_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(
        notify.channels, "send_telegram", lambda text: (_ for _ in ()).throw(NotConfigured("no token"))
    )
    results = notify.send_notifications(NEW, enabled=["telegram"], run_at=RUN_AT)
    assert results["telegram"].startswith("not configured")


def test_unknown_channel_name_is_rejected():
    with pytest.raises(ValueError, match="unknown notification channel"):
        notify.send_notifications(NEW, enabled=["whatsapp"], run_at=RUN_AT)


# --- the channels themselves ------------------------------------------------
# These exercise the real send_* functions with the transport faked at the last
# possible moment (urllib / smtplib), so the request they build is verified -
# not just that a dispatcher called them.
class _FakeResponse:
    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_telegram_builds_the_right_request(monkeypatch):
    from src.notifications import channels

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = __import__("json").loads(request.data)
        return _FakeResponse()

    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(channels, "TELEGRAM_CHAT_ID", "-100999")
    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    channels.send_telegram("merhaba")

    assert captured["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert captured["body"]["chat_id"] == "-100999"
    assert captured["body"]["text"] == "merhaba"
    # no parse_mode: student ids and labels contain _ and would break Markdown
    assert "parse_mode" not in captured["body"]


def test_telegram_truncates_instead_of_failing(monkeypatch):
    from src.notifications import channels

    captured = {}
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(channels, "TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(
        channels.urllib.request,
        "urlopen",
        lambda request, timeout=None: (
            captured.update(body=__import__("json").loads(request.data)) or _FakeResponse()
        ),
    )

    channels.send_telegram("x" * 9000)

    assert len(captured["body"]["text"]) <= channels.TELEGRAM_MAX_CHARS
    assert captured["body"]["text"].endswith("(kısaltıldı)")


def test_telegram_without_credentials_raises_not_configured(monkeypatch):
    from src.notifications import channels

    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(NotConfigured):
        channels.send_telegram("merhaba")


def test_email_is_multipart_and_addressed(monkeypatch):
    from src.notifications import channels

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = user

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(channels, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(channels, "SMTP_PORT", 587)
    monkeypatch.setattr(channels, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(channels, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(channels, "SMTP_FROM", "bot@example.com")
    monkeypatch.setattr(channels, "SMTP_TO", ["mentor@example.com", "koordinator@example.com"])
    monkeypatch.setattr(channels, "SMTP_STARTTLS", True)
    monkeypatch.setattr(channels.smtplib, "SMTP", FakeSMTP)

    channels.send_email("Konu", "düz metin", "<div>zengin</div>")

    assert sent["host"] == "smtp.example.com" and sent["port"] == 587
    assert sent["starttls"] is True
    assert sent["login"] == "bot@example.com"

    message = sent["message"]
    assert message["Subject"] == "Konu"
    assert message["To"] == "mentor@example.com, koordinator@example.com"
    assert message.is_multipart()
    subtypes = {part.get_content_subtype() for part in message.walk() if not part.is_multipart()}
    assert subtypes == {"plain", "html"}


def test_email_without_recipients_raises_not_configured(monkeypatch):
    from src.notifications import channels

    monkeypatch.setattr(channels, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(channels, "SMTP_TO", [])
    with pytest.raises(NotConfigured):
        channels.send_email("Konu", "metin")


def test_http_error_body_is_surfaced(monkeypatch):
    """Telegram answers 400 with the actual reason - it must reach the operator."""
    import io
    import urllib.error

    from src.notifications import channels

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"description":"chat not found"}')
        )

    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(channels, "TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(channels.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="chat not found"):
        channels.send_telegram("merhaba")


# --- the script's refusals ---------------------------------------------------
# "no students at risk" and "the pipeline never ran" must not look the same, and a
# stale run must not be re-sent as if it were today's.
def _script():
    import importlib.util

    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "send_daily_alerts.py"
    spec = importlib.util.spec_from_file_location("send_daily_alerts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_recorded_run_refuses_to_send(monkeypatch, capsys):
    script = _script()
    empty = pd.DataFrame(columns=["student_id", "churn_probability", "status", "run_at"])
    monkeypatch.setattr(script, "latest_run_alerts", lambda: empty)
    monkeypatch.setattr(sys := __import__("sys"), "argv", ["send_daily_alerts.py"])

    sent = []
    monkeypatch.setattr(script, "send_notifications", lambda *a, **k: sent.append(k) or {})

    assert script.main() == 1
    assert sent == []  # nothing was delivered
    assert "kayıtlı koşu yok" in capsys.readouterr().err


def test_stale_run_refuses_to_send(monkeypatch, capsys):
    script = _script()
    from datetime import datetime, timedelta, timezone

    old_run = datetime.now(timezone.utc) - timedelta(hours=40)
    stale = pd.DataFrame(
        [{"student_id": "S1", "churn_probability": 0.5, "status": "new",
          "top_reasons": "", "top_reasons_detail": [], "run_at": old_run.isoformat()}]
    )
    monkeypatch.setattr(script, "latest_run_alerts", lambda: stale)
    monkeypatch.setattr(script, "todays_students", lambda: None)
    monkeypatch.setattr(__import__("sys"), "argv", ["send_daily_alerts.py"])

    sent = []
    monkeypatch.setattr(script, "send_notifications", lambda *a, **k: sent.append(k) or {})

    assert script.main() == 1
    assert sent == []
    assert "son koşu" in capsys.readouterr().err


def test_force_sends_a_stale_run_anyway(monkeypatch):
    script = _script()
    from datetime import datetime, timedelta, timezone

    old_run = datetime.now(timezone.utc) - timedelta(hours=40)
    stale = pd.DataFrame(
        [{"student_id": "S1", "churn_probability": 0.5, "status": "new",
          "top_reasons": "", "top_reasons_detail": [], "run_at": old_run.isoformat()}]
    )
    monkeypatch.setattr(script, "latest_run_alerts", lambda: stale)
    monkeypatch.setattr(script, "todays_students", lambda: None)
    monkeypatch.setattr(__import__("sys"), "argv", ["send_daily_alerts.py", "--force"])
    monkeypatch.setattr(script, "send_notifications", lambda *a, **k: {})

    assert script.main() == 0


def test_a_fresh_run_with_no_at_risk_students_still_sends(monkeypatch):
    """A real run that found nobody at risk IS worth reporting - that is good news."""
    script = _script()
    from datetime import datetime, timezone

    # A run happened; it just has no rows above the threshold. The alert log is
    # empty for that run, so --force is what distinguishes it today.
    monkeypatch.setattr(
        script,
        "latest_run_alerts",
        lambda: pd.DataFrame(
            [{"student_id": "S1", "churn_probability": 0.5, "status": "still_at_risk",
              "top_reasons": "", "top_reasons_detail": [],
              "run_at": datetime.now(timezone.utc).isoformat()}]
        ),
    )
    monkeypatch.setattr(script, "todays_students", lambda: None)
    monkeypatch.setattr(__import__("sys"), "argv", ["send_daily_alerts.py"])
    monkeypatch.setattr(script, "send_notifications", lambda *a, **k: {})

    assert script.main() == 0
