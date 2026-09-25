"""The scheduler service: when it runs, what happens when a run fails (B-14).

No sleeping and no network anywhere. The schedule is a pure function of (now,
RUN_AT, timezone, RUN_DAYS), the failure path is exercised by replacing the two
subprocess steps, and the loop's `sleep` is injected - so the whole service is
testable in milliseconds instead of at 09:00 tomorrow.
"""
import importlib.util
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import scheduling
from src.scheduling import ScheduleConfigError

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "scheduler.py"
_spec = importlib.util.spec_from_file_location("scheduler_script", _PATH)
scheduler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scheduler)

ISTANBUL = ZoneInfo("Europe/Istanbul")
BERLIN = ZoneInfo("Europe/Berlin")
EVERY_DAY = frozenset(range(1, 8))
WEEKDAYS = frozenset({1, 2, 3, 4, 5})


# --- RUN_AT / RUN_DAYS / timezone parsing -----------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("09:00", time(9, 0)), ("00:00", time(0, 0)), ("23:59", time(23, 59)),
     ("  09:30  ", time(9, 30)), ("09:00:30", time(9, 0, 30))],
)
def test_parse_run_at_accepts_a_time_of_day(raw, expected):
    assert scheduling.parse_run_at(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",           # unset
        "9:00",       # single-digit hour - likelier a typo than an intention
        "0900",       # cron-ish
        "24:00",      # not a time
        "09:60",
        "09.00",
        "sabah",
        "9",
        None,
    ],
)
def test_invalid_run_at_is_refused_with_the_value_in_the_message(raw):
    """An unparseable RUN_AT must fail at startup, not fall back to some default.

    A scheduler that quietly picks its own time is worse than one that will not
    start: the container stays up and the run happens at an hour nobody chose.
    """
    with pytest.raises(ScheduleConfigError) as caught:
        scheduling.parse_run_at(raw)
    assert "RUN_AT" in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", EVERY_DAY), ("1-7", EVERY_DAY), ("1-5", WEEKDAYS),
     ("1,3,5", frozenset({1, 3, 5})), (" 6 , 7 ", frozenset({6, 7})),
     ("1-2,5", frozenset({1, 2, 5}))],
)
def test_parse_run_days(raw, expected):
    assert scheduling.parse_run_days(raw) == expected


@pytest.mark.parametrize("raw", ["0-5", "1-8", "8", "5-1", "pazartesi", "1--3", "-"])
def test_invalid_run_days_is_refused(raw):
    with pytest.raises(ScheduleConfigError):
        scheduling.parse_run_days(raw)


def test_unknown_timezone_is_refused_rather_than_defaulting_to_utc():
    """Europe/Istanbul is UTC+3: a silent UTC fallback would move 09:00 to 06:00."""
    with pytest.raises(ScheduleConfigError) as caught:
        scheduling.resolve_timezone("Europe/Istanbul_typo")
    assert "SCHEDULER_TIMEZONE" in str(caught.value)


def test_known_timezone_resolves():
    assert scheduling.resolve_timezone("Europe/Istanbul") == ISTANBUL


# --- next_run_at -------------------------------------------------------------
def test_run_time_later_today():
    now = datetime(2026, 9, 24, 6, 0, tzinfo=ISTANBUL)          # Thursday 06:00
    target = scheduling.next_run_at(now, run_at=time(9, 0), tz=ISTANBUL)
    assert target == datetime(2026, 9, 24, 9, 0, tzinfo=ISTANBUL)


def test_run_time_already_passed_today_goes_to_tomorrow():
    now = datetime(2026, 9, 24, 9, 1, tzinfo=ISTANBUL)
    target = scheduling.next_run_at(now, run_at=time(9, 0), tz=ISTANBUL)
    assert target == datetime(2026, 9, 25, 9, 0, tzinfo=ISTANBUL)


def test_exactly_at_the_run_time_goes_to_tomorrow():
    """Strictly after `now`, or the chain that just finished at 09:00 runs twice."""
    now = datetime(2026, 9, 24, 9, 0, tzinfo=ISTANBUL)
    target = scheduling.next_run_at(now, run_at=time(9, 0), tz=ISTANBUL)
    assert target == datetime(2026, 9, 25, 9, 0, tzinfo=ISTANBUL)


def test_now_in_another_timezone_is_converted_not_ignored():
    """The container's clock is UTC; 07:00 UTC is 10:00 in Istanbul, so 09:00 is gone."""
    now = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)
    target = scheduling.next_run_at(now, run_at=time(9, 0), tz=ISTANBUL)
    assert target == datetime(2026, 9, 25, 9, 0, tzinfo=ISTANBUL)


def test_weekday_only_schedule_skips_the_weekend():
    friday_evening = datetime(2026, 9, 25, 20, 0, tzinfo=ISTANBUL)   # Friday
    target = scheduling.next_run_at(
        friday_evening, run_at=time(9, 0), tz=ISTANBUL, run_days=WEEKDAYS
    )
    assert target == datetime(2026, 9, 28, 9, 0, tzinfo=ISTANBUL)    # Monday
    assert target.isoweekday() == 1


def test_single_day_schedule_wraps_to_next_week():
    monday = datetime(2026, 9, 28, 10, 0, tzinfo=ISTANBUL)
    target = scheduling.next_run_at(
        monday, run_at=time(9, 0), tz=ISTANBUL, run_days=frozenset({1})
    )
    assert target == datetime(2026, 10, 5, 9, 0, tzinfo=ISTANBUL)


def test_dst_fall_back_ambiguous_time_runs_once_at_the_first_occurrence():
    """Europe/Berlin, 25.10.2026: 02:30 happens twice (CEST then CET).

    fold=0 picks the earlier one, so the run fires once. The instant is what
    matters, and it must be the +02:00 one - otherwise "sleep until 02:30" would
    wake an hour late, and a naive implementation could fire on both.
    """
    now = datetime(2026, 10, 25, 1, 0, tzinfo=BERLIN)
    target = scheduling.next_run_at(now, run_at=time(2, 30), tz=BERLIN)
    assert target.utcoffset() == timedelta(hours=2)       # CEST, the first occurrence
    assert target.astimezone(timezone.utc) == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)

    # And the run after it is the next day, not the second 02:30 an hour later.
    following = scheduling.next_run_at(target, run_at=time(2, 30), tz=BERLIN)
    assert following.date() == target.date() + timedelta(days=1)


def test_dst_spring_forward_missing_time_still_runs_that_day():
    """29.03.2026 in Berlin has no 02:30 at all (02:00 -> 03:00).

    The daily run must still happen that day rather than being skipped, so the
    non-existent wall clock is mapped to a real instant one hour on.
    """
    now = datetime(2026, 3, 29, 0, 30, tzinfo=BERLIN)
    target = scheduling.next_run_at(now, run_at=time(2, 30), tz=BERLIN)
    assert target.date() == datetime(2026, 3, 29).date()
    assert target.astimezone(timezone.utc) == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)


def test_istanbul_has_no_dst_so_the_offset_never_moves():
    """Turkey is permanently UTC+3 since 2016 - pinned, because the default depends on it."""
    winter = scheduling.next_run_at(
        datetime(2026, 1, 15, 6, 0, tzinfo=ISTANBUL), run_at=time(9, 0), tz=ISTANBUL
    )
    summer = scheduling.next_run_at(
        datetime(2026, 7, 15, 6, 0, tzinfo=ISTANBUL), run_at=time(9, 0), tz=ISTANBUL
    )
    assert winter.utcoffset() == summer.utcoffset() == timedelta(hours=3)


def test_seconds_until_is_never_negative():
    now = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    past = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)
    assert scheduling.seconds_until(now, past) == 0.0
    assert scheduling.seconds_until(past, now) == 3600.0


def test_describe_schedule_mentions_the_days_when_they_are_restricted():
    every = scheduling.describe_schedule(run_at=time(9, 0), tz=ISTANBUL, run_days=EVERY_DAY)
    weekdays = scheduling.describe_schedule(run_at=time(9, 0), tz=ISTANBUL, run_days=WEEKDAYS)
    assert "her gün" in every
    assert "Pzt" in weekdays and "Cmt" not in weekdays


# --- The state file ----------------------------------------------------------
@pytest.fixture
def state_path(tmp_path, monkeypatch):
    path = tmp_path / "state" / "scheduler_state.json"
    monkeypatch.setattr(scheduler, "SCHEDULER_STATE_PATH", str(path))
    return path


def test_state_survives_a_round_trip_and_creates_its_directory(state_path):
    now = datetime(2026, 9, 24, 6, 5, tzinfo=timezone.utc)
    scheduling.record_success(state_path, now=now)
    assert scheduling.parse_timestamp(
        scheduling.read_state(state_path)["last_success_at"]
    ) == now


def test_a_success_clears_the_previous_failure(state_path):
    scheduling.record_failure(
        state_path, now=datetime(2026, 9, 23, 6, 0, tzinfo=timezone.utc),
        step="pipeline", message="boom",
    )
    scheduling.record_success(state_path, now=datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc))
    state = scheduling.read_state(state_path)
    assert "last_failure_at" not in state and "last_failure_step" not in state


def test_unreadable_state_is_treated_as_empty(state_path):
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not json")
    assert scheduling.read_state(state_path) == {}


def test_failure_message_is_bounded(state_path):
    scheduling.record_failure(
        state_path, now=datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc),
        step="pipeline", message="x" * 50_000,
    )
    assert len(scheduling.read_state(state_path)["last_failure_message"]) <= 2000


# --- The heartbeat decision --------------------------------------------------
NOW = datetime(2026, 9, 24, 9, 30, tzinfo=timezone.utc)


def test_heartbeat_silent_after_a_recent_success():
    assert scheduling.heartbeat_reason(
        now=NOW, last_success_at=NOW - timedelta(hours=23), max_hours=24
    ) is None


def test_heartbeat_alerts_when_the_last_success_is_too_old():
    reason = scheduling.heartbeat_reason(
        now=NOW, last_success_at=NOW - timedelta(hours=25), max_hours=24
    )
    assert reason is not None and "25 saat" in reason


def test_heartbeat_window_is_configurable():
    older = NOW - timedelta(hours=30)
    assert scheduling.heartbeat_reason(now=NOW, last_success_at=older, max_hours=24) is not None
    assert scheduling.heartbeat_reason(now=NOW, last_success_at=older, max_hours=74) is None


def test_a_fresh_deployment_does_not_alert_before_the_first_run_is_due():
    """`up -d` at 08:00 must not page anyone at 08:00 for a 09:00 run."""
    assert scheduling.heartbeat_reason(
        now=NOW, last_success_at=None, first_seen_at=NOW - timedelta(hours=1), max_hours=24
    ) is None


def test_a_scheduler_that_has_never_succeeded_alerts_once_the_window_has_passed():
    reason = scheduling.heartbeat_reason(
        now=NOW, last_success_at=None, first_seen_at=NOW - timedelta(hours=30), max_hours=24
    )
    assert reason is not None and "Hiç başarılı koşu" in reason


def test_heartbeat_does_not_repeat_inside_the_same_window():
    """A crash-restart loop must not send one alert per restart."""
    assert scheduling.heartbeat_reason(
        now=NOW,
        last_success_at=NOW - timedelta(hours=50),
        last_alert_at=NOW - timedelta(hours=2),
        max_hours=24,
    ) is None
    assert scheduling.heartbeat_reason(
        now=NOW,
        last_success_at=NOW - timedelta(hours=50),
        last_alert_at=NOW - timedelta(hours=25),
        max_hours=24,
    ) is not None


# --- The chain and the failure path -----------------------------------------
@pytest.fixture
def ops_inbox(monkeypatch):
    """Collect what would have gone to the operator channel."""
    sent: list[str] = []
    monkeypatch.setattr(scheduler, "ops_channels_configured", lambda: ["webhook"])
    monkeypatch.setattr(
        scheduler, "send_ops_alert", lambda text: sent.append(text) or {"webhook": "sent"}
    )
    return sent


def _steps(monkeypatch, outcomes):
    """Replace the subprocess steps with recorded outcomes. Nothing is executed."""
    calls: list[str] = []

    def fake_run_step(name, command, *, timeout):
        calls.append(name)
        return outcomes[name]

    monkeypatch.setattr(scheduler, "run_step", fake_run_step)
    return calls


def test_a_successful_chain_runs_both_steps_and_records_the_success(
    monkeypatch, state_path, ops_inbox
):
    calls = _steps(monkeypatch, {"pipeline": (True, ""), "alerts": (True, "")})

    assert scheduler.run_chain() is True
    assert calls == ["pipeline", "alerts"]
    assert ops_inbox == []
    assert "last_success_at" in scheduling.read_state(state_path)


def test_a_failed_pipeline_never_reaches_the_customer(monkeypatch, state_path, ops_inbox):
    """The `&&` guarantee: no partial message when scoring failed."""
    calls = _steps(
        monkeypatch,
        {"pipeline": (False, "exit code 1\nValueError: veri yok"), "alerts": (True, "")},
    )

    assert scheduler.run_chain() is False
    assert calls == ["pipeline"], "send_daily_alerts must not run after a failed pipeline"

    assert len(ops_inbox) == 1
    message = ops_inbox[0]
    assert "BAŞARISIZ" in message and "pipeline" in message
    assert "ValueError" in message                      # diagnosable
    assert "GİTMEDİ" in message                         # says the customer got nothing

    state = scheduling.read_state(state_path)
    assert state["last_failure_step"] == "pipeline"
    assert "last_success_at" not in state


def test_a_failed_alert_send_is_also_an_ops_failure(monkeypatch, state_path, ops_inbox):
    """The run scored fine but nobody was told - that is an outage, not a success."""
    calls = _steps(
        monkeypatch, {"pipeline": (True, ""), "alerts": (False, "exit code 1\nchat not found")}
    )

    assert scheduler.run_chain() is False
    assert calls == ["pipeline", "alerts"]
    assert "alerts" in ops_inbox[0]
    assert "last_success_at" not in scheduling.read_state(state_path)


def test_the_failure_is_recorded_even_with_no_ops_channel(monkeypatch, state_path, caplog):
    """No channel configured: the log and the health status are the whole signal."""
    _steps(monkeypatch, {"pipeline": (False, "exit code 1\nboom")})
    monkeypatch.setattr(scheduler, "ops_channels_configured", lambda: [])
    monkeypatch.setattr(
        scheduler, "send_ops_alert", lambda text: pytest.fail("nothing to send to")
    )

    with caplog.at_level("ERROR"):
        assert scheduler.run_chain() is False

    assert "NOT DELIVERED" in caplog.text
    assert "BAŞARISIZ" in caplog.text
    assert scheduling.read_state(state_path)["last_failure_step"] == "pipeline"
    assert scheduler.healthcheck() == 1


# --- The healthcheck (what `docker compose ps` shows) ------------------------
def test_healthcheck_is_green_after_a_recent_success(monkeypatch, state_path):
    scheduling.record_success(state_path, now=datetime.now(timezone.utc))
    assert scheduler.healthcheck() == 0


def test_healthcheck_is_red_on_a_stale_success(monkeypatch, state_path):
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    scheduling.record_success(state_path, now=old)
    assert scheduler.healthcheck() == 1


def test_healthcheck_is_not_silenced_by_an_already_sent_heartbeat_alert(state_path):
    """The alert stamp spares the ops channel; it must not make the container look well."""
    now = datetime.now(timezone.utc)
    scheduling.record_success(state_path, now=now - timedelta(hours=48))
    scheduling.record_heartbeat_alert(state_path, now=now)
    assert scheduler.healthcheck() == 1


def test_a_fresh_deployment_is_healthy(state_path):
    scheduling.note_started(state_path, now=datetime.now(timezone.utc))
    assert scheduler.healthcheck() == 0


# --- The loop ----------------------------------------------------------------
def test_the_loop_sleeps_until_the_due_time_then_runs(monkeypatch, state_path, ops_inbox):
    """No real sleep: the injected one records how long it was asked to wait."""
    slept: list[float] = []
    ran: list[str] = []
    monkeypatch.setattr(scheduler, "run_chain", lambda: ran.append("chain") or True)
    monkeypatch.setattr(scheduler, "check_heartbeat", lambda **kwargs: None)

    assert scheduler.loop(sleep=slept.append, max_cycles=2) == 0
    assert ran == ["chain", "chain"]
    assert len(slept) == 2
    # Anything over 24h would mean the next-run computation lost a day.
    assert all(0 <= wait <= 24 * 3600 for wait in slept)


def test_the_loop_checks_the_heartbeat_before_the_first_sleep(monkeypatch, state_path):
    """A container that was down for two days says so at startup, not at 09:00."""
    checks: list[str] = []
    monkeypatch.setattr(scheduler, "check_heartbeat", lambda **kwargs: checks.append("checked"))
    monkeypatch.setattr(scheduler, "run_chain", lambda: True)

    scheduler.loop(sleep=lambda seconds: None, max_cycles=0)
    assert checks == ["checked"]


def test_a_bad_run_at_stops_the_loop_instead_of_guessing(monkeypatch, state_path):
    monkeypatch.setattr(scheduler, "RUN_AT", "sabah 9")
    with pytest.raises(ScheduleConfigError):
        scheduler.loop(sleep=lambda seconds: None, max_cycles=1)


# --- The operator channel is not the customer channel -----------------------
def test_ops_alert_goes_to_the_ops_chat_not_the_customers(monkeypatch):
    from src.notifications import channels, ops

    monkeypatch.setattr(ops, "TELEGRAM_BOT_TOKEN", "8123456789:AAFakeTokenForTestsOnly_abcdef")
    monkeypatch.setattr(ops, "TELEGRAM_CHAT_ID", "-100customer")
    monkeypatch.setattr(ops, "OPS_TELEGRAM_CHAT_ID", "-100ops")
    monkeypatch.setattr(ops, "OPS_ALERT_WEBHOOK_URL", None)

    sent = {}
    monkeypatch.setattr(
        channels, "send_telegram", lambda text, *, chat_id=None: sent.update(text=text, chat=chat_id)
    )

    assert ops.send_ops_alert("koşu başarısız") == {"telegram": "sent"}
    assert sent["chat"] == "-100ops"
    assert ops.OPS_PREFIX in sent["text"]


def test_ops_webhook_is_used_when_configured(monkeypatch):
    from src.notifications import channels, ops

    monkeypatch.setattr(ops, "OPS_TELEGRAM_CHAT_ID", None)
    monkeypatch.setattr(ops, "OPS_ALERT_WEBHOOK_URL", "https://hooks.example/ops")

    sent = {}
    monkeypatch.setattr(channels, "send_webhook", lambda text, *, url=None: sent.update(url=url))

    assert ops.send_ops_alert("koşu başarısız") == {"webhook": "sent"}
    assert sent["url"] == "https://hooks.example/ops"


def test_no_ops_channel_configured_sends_nothing(monkeypatch):
    from src.notifications import ops

    monkeypatch.setattr(ops, "OPS_TELEGRAM_CHAT_ID", None)
    monkeypatch.setattr(ops, "OPS_ALERT_WEBHOOK_URL", None)
    assert ops.ops_channels_configured() == []
    assert ops.send_ops_alert("koşu başarısız") == {}


def test_a_broken_ops_channel_is_reported_not_raised(monkeypatch):
    """Reporting a failure must never fail: an exception here loses the failure."""
    from src.notifications import channels, ops

    monkeypatch.setattr(ops, "OPS_TELEGRAM_CHAT_ID", None)
    monkeypatch.setattr(ops, "OPS_ALERT_WEBHOOK_URL", "https://hooks.example/ops")

    def boom(text, *, url=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(channels, "send_webhook", boom)
    results = ops.send_ops_alert("koşu başarısız")
    assert results["webhook"].startswith("error:")
    assert "connection reset" in results["webhook"]


def test_the_ops_webhook_url_is_treated_as_a_secret(monkeypatch):
    """It IS the credential, and it appears in the error of the alert about a failed run."""
    from src.notifications import channels

    hook = "https://hooks.slack.com/services/T000/B000/OPSOPSOPSOPSOPSOPSOPS"
    monkeypatch.setattr(channels, "OPS_ALERT_WEBHOOK_URL", hook)
    cleaned = channels.redact(f"URLError: timed out calling {hook}")
    assert hook not in cleaned
    assert channels.REDACTED in cleaned


def test_the_database_password_is_treated_as_a_secret(monkeypatch):
    """A failed run's raw output now reaches an operator channel; the DSN may be in it."""
    from src.notifications import channels

    dsn = "postgresql://eo:Sup3rSecretPassword@db:5432/eo_churn"
    monkeypatch.setattr(channels, "DATABASE_URL", dsn)
    cleaned = channels.redact(f"OperationalError: could not connect to {dsn}")
    assert "Sup3rSecretPassword" not in cleaned
    assert channels.REDACTED in cleaned


def test_run_step_output_is_redacted_before_it_is_reported(monkeypatch, tmp_path):
    """A child's traceback may quote a token; it goes to the log and to Telegram."""
    from src.notifications import channels

    token = "8123456789:AAFakeBotTokenFromDotEnv_abcdefghijk"
    monkeypatch.setattr(channels, "TELEGRAM_BOT_TOKEN", token)

    script = tmp_path / "leaky.py"
    script.write_text(f"import sys; print('failed calling /bot{token}/sendMessage'); sys.exit(1)\n")

    ok, detail = scheduler.run_step("pipeline", [__import__("sys").executable, str(script)], timeout=60)
    assert ok is False
    assert token not in detail
    assert channels.REDACTED in detail
