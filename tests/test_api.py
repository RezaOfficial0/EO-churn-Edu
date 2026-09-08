"""FastAPI smoke tests against the committed model, using Starlette's TestClient
(no live server needed)."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import API_KEY, DAILY_DATA_PATH, FEATURE_BOUNDS, FEATURES, CAT_COLS


@pytest.fixture(scope="module")
def client():
    # If the developer's .env sets an API key, send it on every request so the
    # suite passes whether or not auth is enabled.
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    with TestClient(app, headers=headers, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.skipif(not API_KEY, reason="auth disabled (no API_KEY set)")
def test_missing_api_key_is_rejected():
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get("/health").status_code == 200
        assert anonymous.get("/metrics").status_code == 401


def _valid_predict_body() -> dict:
    """A raw student row that satisfies every FEATURE_BOUNDS constraint."""
    row = pd.read_csv(DAILY_DATA_PATH).iloc[0]
    body = {}
    for feature in FEATURES:
        if feature in CAT_COLS:
            body[feature] = str(row[feature])
        elif feature in row and pd.notna(row[feature]):
            body[feature] = float(row[feature])
        else:
            low, high = FEATURE_BOUNDS[feature]
            body[feature] = float(low)
    return body


def test_health_ok(client):
    assert client.get("/health").json()["status"] == "ok"


def test_predict_happy_path(client):
    response = client.post("/predict", json=_valid_predict_body())
    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["churn_probability"] <= 1.0
    assert len(body["top_reasons"]) == 3


def test_predict_rejects_out_of_range_value(client):
    body = _valid_predict_body()
    body["monthly_fee_try"] = 1e18
    assert client.post("/predict", json=body).status_code == 422


def test_predict_rejects_missing_field(client):
    body = _valid_predict_body()
    del body["tenure_months"]
    assert client.post("/predict", json=body).status_code == 422


def test_predict_by_student_id_and_404(client):
    known_id = str(pd.read_csv(DAILY_DATA_PATH).iloc[0]["student_id"])
    response = client.get(f"/predict/{known_id}")
    assert response.status_code == 200
    assert set(FEATURES) <= response.json()["features"].keys()
    assert client.get("/predict/not-a-real-id").status_code == 404


def test_run_daily_pipeline(client):
    body = client.post("/run-daily-pipeline").json()
    assert "churn_risk_count" in body
    if body["students"]:
        first = body["students"][0]
        assert {"status", "churn_probability", "top_reasons", "features"} <= first.keys()
        assert set(FEATURES) <= first["features"].keys()


def test_metrics_reports_synthetic_flag(client):
    body = client.get("/metrics").json()
    assert body["is_synthetic_data"] is True
    assert "chosen_threshold" in body


# --- GET /students (read-only scoring) --------------------------------------
def _alert_log_fingerprint():
    from config import DAILY_ALERTS_PATH
    from pathlib import Path as _Path

    path = _Path(DAILY_ALERTS_PATH)
    return path.stat().st_size if path.exists() else None


def test_students_is_read_only(client):
    """The dashboard hits this on every page load; it must not touch the alert log."""
    before = _alert_log_fingerprint()
    response = client.get("/students", params={"threshold": 0})
    assert response.status_code == 200
    assert _alert_log_fingerprint() == before


def test_students_response_shape(client):
    body = client.get("/students", params={"threshold": 0}).json()

    assert set(body) == {"count", "threshold", "students"}
    assert body["threshold"] == 0
    assert body["count"] == len(body["students"])
    assert body["count"] == len(pd.read_csv(DAILY_DATA_PATH))  # threshold 0 keeps everyone

    student = body["students"][0]
    assert {
        "student_id",
        "enrollment_date",
        "churn_probability",
        "top_reasons",
        "top_reasons_detail",
        "features",
    } <= set(student)
    assert "status" not in student  # status only means something for a recorded run
    probabilities = [s["churn_probability"] for s in body["students"]]
    assert probabilities == sorted(probabilities, reverse=True)
