"""FastAPI smoke tests against the committed model, using Starlette's TestClient
(no live server needed)."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import DAILY_DATA_PATH, FEATURE_BOUNDS, FEATURES, CAT_COLS


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


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
    assert client.get(f"/predict/{known_id}").status_code == 200
    assert client.get("/predict/not-a-real-id").status_code == 404


def test_run_daily_pipeline(client):
    body = client.post("/run-daily-pipeline").json()
    assert "churn_risk_count" in body
    if body["students"]:
        assert {"status", "churn_probability", "top_reasons"} <= body["students"][0].keys()


def test_metrics_reports_synthetic_flag(client):
    body = client.get("/metrics").json()
    assert body["is_synthetic_data"] is True
    assert "chosen_threshold" in body
