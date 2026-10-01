"""The campaign store is a trust boundary: a separate service writes to it.

These tests pin the two things the dashboard depends on (newest-per-student wins,
a torn line does not take out the panel) and the three things that keep a remote
writer honest (auth, bounds, no unexpected keys).
"""
import json

import pytest
from fastapi.testclient import TestClient

import src.data.campaigns as campaigns_module
from api.main import app
from config import API_KEY
from src.data.campaigns import (
    append_campaign,
    clear_campaigns,
    latest_campaign_by_student,
    read_campaigns,
)


def _campaign(student_id="STU300016", campaign_id="c-1", message="Merhaba"):
    return {
        "student_id": student_id,
        "campaign_id": campaign_id,
        "priority": "high",
        "risk_summary": "Yanit suresi uzadi",
        "strategy": "Mentor temasi",
        "campaign_steps": [
            {
                "step": 1,
                "timing": "24 saat icinde",
                "owner": "Mentor",
                "channel": "telefon",
                "action_id": "mentor_check_in",
                "objective": "Durum kontrolu",
                "message_draft": message,
                "success_criteria": ["Gorusme yapildi"],
                "policy_source_ids": ["retention_policy:1"],
            }
        ],
        "approved_offer_ids": [],
        "policy_source_ids": ["retention_policy:1"],
        "missing_information": [],
        "warnings": [],
        "requires_human_review": True,
        "generated_by": "retention_app",
        "model": "gemma-4-12b",
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "campaigns.jsonl")
    monkeypatch.setattr(campaigns_module, "CAMPAIGNS_PATH", path)
    return path


@pytest.fixture
def client(store):
    with TestClient(app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as c:
        yield c


# --- store ------------------------------------------------------------------

def test_missing_file_is_empty_not_an_error(store):
    assert read_campaigns(store) == []
    assert latest_campaign_by_student(store) == {}


def test_regenerating_keeps_the_old_draft_and_the_new_one_wins(store):
    append_campaign(_campaign(campaign_id="c-1", message="ilk"), store)
    append_campaign(_campaign(campaign_id="c-2", message="ikinci"), store)

    assert len(read_campaigns(store)) == 2, "the log must not overwrite"
    latest = latest_campaign_by_student(store)
    assert len(latest) == 1
    assert latest["STU300016"]["campaign_id"] == "c-2"


def test_stored_at_is_set_by_us_not_by_the_caller(store):
    record = append_campaign({**_campaign(), "stored_at": "1999-01-01T00:00:00Z"}, store)
    assert record["stored_at"].startswith("20")
    assert record["stored_at"] != "1999-01-01T00:00:00Z"


def test_a_torn_line_is_skipped_not_fatal(store):
    append_campaign(_campaign(student_id="STU1", campaign_id="c-1"), store)
    with open(store, "a", encoding="utf-8") as handle:
        handle.write('{"student_id": "STU2", "campaign_i\n')  # killed mid-write
    append_campaign(_campaign(student_id="STU3", campaign_id="c-3"), store)

    latest = latest_campaign_by_student(store)
    assert set(latest) == {"STU1", "STU3"}


def test_each_campaign_is_exactly_one_line(store):
    append_campaign(_campaign(message="iki\nsatirli mesaj"), store)
    lines = [line for line in open(store, encoding="utf-8").read().splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["campaign_steps"][0]["message_draft"] == "iki\nsatirli mesaj"


def test_clear_reports_what_it_dropped(store):
    append_campaign(_campaign(student_id="STU1"), store)
    append_campaign(_campaign(student_id="STU2"), store)
    assert clear_campaigns(store) == 2
    assert read_campaigns(store) == []


# --- endpoints --------------------------------------------------------------

def test_post_then_get_round_trips(client):
    posted = client.post("/campaigns", json=_campaign())
    assert posted.status_code == 201
    assert posted.json()["status"] == "stored"

    listed = client.get("/campaigns").json()
    assert listed["count"] == 1
    assert listed["campaigns"][0]["student_id"] == "STU300016"

    one = client.get("/campaigns/STU300016").json()
    assert one["campaign_steps"][0]["message_draft"] == "Merhaba"
    assert one["stored_at"]


def test_unknown_student_is_404_without_echoing_the_id(client):
    response = client.get("/campaigns/STU-does-not-exist-9988")
    assert response.status_code == 404
    assert "STU-does-not-exist-9988" not in response.text


def test_every_campaign_route_needs_the_api_key(store):
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.post("/campaigns", json=_campaign()).status_code == 401
        assert anonymous.get("/campaigns").status_code == 401
        assert anonymous.get("/campaigns/STU300016").status_code == 401


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c.update(unexpected="x"), id="unexpected_key"),
        pytest.param(lambda c: c.update(priority="urgent"), id="priority_not_in_literal"),
        pytest.param(lambda c: c.update(campaign_steps=[]), id="no_steps"),
        pytest.param(lambda c: c.update(student_id=""), id="blank_student_id"),
        pytest.param(lambda c: c.update(risk_summary="x" * 5000), id="text_too_long"),
        pytest.param(lambda c: c["campaign_steps"][0].update(step=0), id="step_below_one"),
        pytest.param(lambda c: c["campaign_steps"][0].update(step=1.5), id="step_not_an_int"),
        pytest.param(
            lambda c: c["campaign_steps"][0].update(message_draft=""), id="blank_message"
        ),
    ],
)
def test_a_malformed_campaign_is_refused(client, mutate):
    body = _campaign()
    mutate(body)
    assert client.post("/campaigns", json=body).status_code == 422


def test_error_body_is_a_string_not_a_pydantic_dump(client):
    body = _campaign()
    body["priority"] = "urgent"
    assert isinstance(client.post("/campaigns", json=body).json()["detail"], str)
