"""B-12: the access log must not carry student ids, and must be correlatable.

The old access line was `GET /predict/STU300001 -> 200`, which put a plain-text
identifier for a (mostly under-age) person into an unrotated, unmasked log - and it
was also the only way to tie a traceback to the request that produced it.
"""
import logging

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import API_KEY, DAILY_DATA_PATH
from src.logging_setup import _RequestIdFilter


class _Capture(logging.Handler):
    """Root handler that records log records with `request_id` filled in.

    The id is added by a filter on the real handler, so a test handler has to carry
    the same filter to see what an operator would see.
    """

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []
        self.addFilter(_RequestIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def captured_logs():
    handler = _Capture()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as c:
        yield c


def _access_lines(handler: _Capture) -> list[logging.LogRecord]:
    return [record for record in handler.records if " -> " in record.getMessage()]


def test_access_log_writes_the_route_template_not_the_student_id(client, captured_logs):
    student_id = str(pd.read_csv(DAILY_DATA_PATH).iloc[0]["student_id"])

    assert client.get(f"/predict/{student_id}").status_code == 200

    lines = _access_lines(captured_logs)
    assert lines, "no access log line was written"
    message = lines[-1].getMessage()
    assert "/predict/{student_id}" in message
    assert student_id not in message


def test_an_unmatched_path_is_truncated_to_its_first_segment(client, captured_logs):
    client.get("/no-such-route/STU999999")

    message = _access_lines(captured_logs)[-1].getMessage()
    assert "STU999999" not in message
    assert "/no-such-route/..." in message


def test_every_line_carries_a_request_id(client, captured_logs):
    client.get("/health")

    line = _access_lines(captured_logs)[-1]
    assert line.request_id != "-"
    assert len(line.request_id) == 12


def test_a_traceback_and_its_access_line_share_one_request_id(
    client, captured_logs, monkeypatch
):
    """Without this, a 500 cannot be tied to the request that caused it - which is
    what the student id in the path used to be used for."""

    def explode(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("api.main.score_students", explode)

    assert client.get("/students").status_code == 500

    access = _access_lines(captured_logs)[-1]
    tracebacks = [record for record in captured_logs.records if record.exc_info]
    assert tracebacks, "the unhandled-exception handler logged no traceback"
    assert tracebacks[-1].request_id == access.request_id != "-"


def test_two_requests_get_different_ids(client, captured_logs):
    client.get("/health")
    client.get("/health")

    ids = [record.request_id for record in _access_lines(captured_logs)]
    assert len(set(ids)) == 2
