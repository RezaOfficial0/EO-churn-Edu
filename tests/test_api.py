"""FastAPI smoke tests against the committed model, using Starlette's TestClient
(no live server needed)."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import METRICS_PUBLIC_FIELDS, app
from config import (
    API_KEY,
    CAT_COLS,
    CATEGORICAL_LEVELS,
    DAILY_DATA_PATH,
    FEATURE_BOUNDS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
)
from src.data.features import add_monthly_value


@pytest.fixture(scope="module")
def client():
    # tests/conftest.py guarantees an API_KEY, so auth is always exercised.
    with TestClient(app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def anonymous_client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# Every route that returns student data or model internals. /health is the only
# endpoint deliberately left open.
_PROTECTED_REQUESTS = [
    ("GET", "/students"),
    ("GET", "/metrics"),
    ("GET", "/predict/STU300001"),
    ("POST", "/predict"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
]


@pytest.mark.parametrize("method,path", _PROTECTED_REQUESTS)
def test_missing_api_key_is_rejected(anonymous_client, method, path):
    assert anonymous_client.request(method, path).status_code == 401


@pytest.mark.parametrize("method,path", _PROTECTED_REQUESTS)
def test_wrong_api_key_is_rejected(anonymous_client, method, path):
    response = anonymous_client.request(method, path, headers={"X-API-Key": "not-the-key"})
    assert response.status_code == 401


def test_health_needs_no_key_and_leaks_no_paths(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert set(response.json()) <= {"status", "components"}
    assert "reason" not in response.json()


def test_docs_are_served_with_the_key(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def _valid_predict_body() -> dict:
    """A raw student row that satisfies every FEATURE_BOUNDS constraint.

    Run through add_monthly_value first: POST /predict takes MODEL features, and
    the derived ones are not in the raw file. Counters and flags are sent as `int`
    because that is what the schema now requires (B-10) - a float there is exactly
    the "2.7 support tickets" case the endpoint has to refuse.
    """
    row = add_monthly_value(pd.read_csv(DAILY_DATA_PATH)).iloc[0]
    body = {}
    for feature in FEATURES:
        if feature in CAT_COLS:
            body[feature] = str(row[feature])
            continue
        if feature in row and pd.notna(row[feature]):
            value = float(row[feature])
        else:
            value = float(FEATURE_BOUNDS[feature][0])
        body[feature] = int(value) if feature in INTEGER_FEATURES + FLAG_FEATURES else value
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
    body["monthly_value_try"] = 1e18
    assert client.post("/predict", json=body).status_code == 422


def test_predict_rejects_missing_field(client):
    body = _valid_predict_body()
    del body["tenure_months"]
    assert client.post("/predict", json=body).status_code == 422


# --- Input hardening (B-10) -------------------------------------------------
def _rejected(client, **overrides) -> None:
    body = {**_valid_predict_body(), **overrides}
    # json.dumps, not `json=`: httpx refuses to encode NaN, while the stdlib writes
    # the bare `NaN` literal - which is exactly what a client can send and what
    # Starlette's json.loads happily accepts.
    response = client.post(
        "/predict",
        content=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, (overrides, response.json())
    # The contract promises a single string, and it must not echo the value back.
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("threshold", ["nan", "-inf", "inf", "-0.1", "1.1", "2", "banana"])
def test_students_rejects_threshold_outside_zero_to_one(client, threshold):
    """`?threshold=nan` used to 500, and `?threshold=-inf` ran SHAP over every row
    before doing so."""
    response = client.get("/students", params={"threshold": threshold})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_predict_rejects_unknown_category(client):
    """CatBoost hashes an unseen category into a plausible probability, so an
    unlisted value has to be refused before it gets there."""
    _rejected(client, plan_type="banana")


def test_predict_rejects_empty_category(client):
    _rejected(client, grade="")


def test_predict_rejects_non_finite_numeric(client):
    _rejected(client, tenure_months=float("nan"))
    _rejected(client, tenure_months=float("inf"))


def test_predict_rejects_boolean_for_a_numeric_field(client):
    """pydantic's lax mode would read True as 1.0."""
    _rejected(client, program_adherence_rate=True)


@pytest.mark.parametrize("feature", INTEGER_FEATURES)
def test_predict_rejects_fractional_counter(client, feature):
    _rejected(client, **{feature: 2.7})


@pytest.mark.parametrize("feature", FLAG_FEATURES)
def test_predict_rejects_half_a_flag(client, feature):
    _rejected(client, **{feature: 0.5})
    _rejected(client, **{feature: True})


def test_predict_rejects_an_extra_field(client):
    """validation.py refuses an extra column because it would become feature #25;
    the API used to drop it silently."""
    _rejected(client, churn=1)


def test_predict_accepts_every_configured_category_level(client):
    """A level listed in config must actually be accepted - otherwise the Literal
    would quietly reject real students."""
    for column, levels in CATEGORICAL_LEVELS.items():
        for level in levels:
            body = {**_valid_predict_body(), column: level}
            assert client.post("/predict", json=body).status_code == 200, (column, level)


def test_validation_error_body_is_a_string_not_a_list(client):
    body = _valid_predict_body()
    del body["tenure_months"]
    del body["grade"]
    detail = client.post("/predict", json=body).json()["detail"]
    assert isinstance(detail, str)
    assert "tenure_months" in detail and "grade" in detail


def test_predict_by_student_id_and_404(client):
    known_id = str(pd.read_csv(DAILY_DATA_PATH).iloc[0]["student_id"])
    response = client.get(f"/predict/{known_id}")
    assert response.status_code == 200
    assert set(FEATURES) <= response.json()["features"].keys()
    assert client.get("/predict/not-a-real-id").status_code == 404


# --- Null id columns serialise as null (B-11) -------------------------------
def test_missing_enrollment_date_is_null_not_a_500(client, tmp_path, monkeypatch):
    """One empty `enrollment_date` used to take out every endpoint that returns it.

    `require_no_nulls` covers FEATURES, not STUDENT_INFO, and nothing imputes an
    enrollment date - correctly, it is not a model input. So the value reached the
    response as a float NaN, which Starlette refuses to encode, and both endpoints
    answered `{"detail": "internal server error"}`.
    """
    daily = pd.read_csv(DAILY_DATA_PATH)
    daily.loc[0, "enrollment_date"] = None
    student_id = str(daily.loc[0, "student_id"])
    path = tmp_path / "daily_with_a_missing_date.csv"
    daily.to_csv(path, index=False)
    monkeypatch.setattr("api.main.DAILY_DATA_PATH", str(path))

    one = client.get(f"/predict/{student_id}")
    assert one.status_code == 200
    assert one.json()["enrollment_date"] is None

    listed = client.get("/students", params={"threshold": 0})
    assert listed.status_code == 200
    affected = [s for s in listed.json()["students"] if s["student_id"] == student_id]
    assert affected and affected[0]["enrollment_date"] is None

    # POST /predict takes no id columns, so it cannot hit the same NaN - but it is
    # the third response path and must keep working with the same serialisation.
    assert client.post("/predict", json=_valid_predict_body()).status_code == 200


def test_run_daily_pipeline_endpoint_is_gone(client):
    """B-08: the day's run belongs to the scheduler, not to an HTTP verb.

    This used to be the API's only write endpoint, so it was also the only way a
    double-clicked button could overwrite the baseline that `new` / `still_at_risk`
    is measured against.
    """
    assert client.post("/run-daily-pipeline").status_code in (404, 405)


def test_no_route_writes_to_the_alert_log(client):
    """Nothing reachable over HTTP may leave a row in the alert log."""
    before = _alert_log_fingerprint()
    for method, path in _PROTECTED_REQUESTS:
        client.request(method, path)
    client.get("/health")
    assert _alert_log_fingerprint() == before


def test_metrics_reports_synthetic_flag(client):
    body = client.get("/metrics").json()
    assert body["is_synthetic_data"] is True
    assert "chosen_threshold" in body


# --- GET /metrics allow-list (B-09) ----------------------------------------
def test_metrics_returns_only_allow_listed_fields(client):
    body = client.get("/metrics").json()
    assert set(body) <= set(METRICS_PUBLIC_FIELDS)
    # The fields that used to leak: absolute paths, the data hash, the training
    # medians, the hyperparameters, and the per-segment false-negative breakdown.
    for field in (
        "data_file",
        "calibrator_path",
        "data_sha256",
        "imputation_values",
        "model_params",
        "catboost_tree_count",
        "error_analysis_false_negatives",
        "threshold_selection",
    ):
        assert field not in body


def test_a_new_meta_field_does_not_leak(client):
    """An allow-list, not a deny-list: a key added to model_meta.json tomorrow must
    not appear in the response just because nobody remembered to exclude it."""
    meta = client.app.state.meta
    client.app.state.meta = {**meta, "secret_customer_note": "do not publish"}
    try:
        assert "secret_customer_note" not in client.get("/metrics").json()
    finally:
        client.app.state.meta = meta


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

    assert set(body) == {"count", "skipped_count", "threshold", "students"}
    assert body["threshold"] == 0
    assert body["count"] == len(body["students"])
    # threshold 0 keeps everyone the run could score; the sample data has no unusable
    # rows, so scored + skipped is the whole file (B-28).
    assert body["skipped_count"] == 0
    assert body["count"] == len(pd.read_csv(DAILY_DATA_PATH))

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
