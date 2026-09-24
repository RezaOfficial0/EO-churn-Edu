"""B-20: readiness has to be honest, and a missing calibrator has to fail closed.

`app.state.model` used to be assigned before the explainer, the calibrator and the
meta were loaded, so a failure in any of those three left `/health` answering "ok"
while every scoring call returned 500 - and docker-compose's healthcheck reads
`/health`, so a permanently broken container stayed "healthy".
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import (
    API_KEY,
    CALIBRATOR_PATH,
    CAT_COLS,
    CATEGORICAL_LEVELS,
    FEATURE_BOUNDS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
    MODEL_META_PATH,
)
from src.model.calibrate import CalibratorMissingError, load_calibrator
from src.model.load import check_meta_matches_config, load_meta


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as c:
        yield c


def _minimal_predict_body() -> dict:
    """Schema-valid values only, so the request fails on readiness and not on the body.

    Built from config rather than from the daily CSV: this test is about what the
    service does when it is not ready, not about any particular student.
    """
    body = {}
    for feature in FEATURES:
        if feature in CAT_COLS:
            body[feature] = CATEGORICAL_LEVELS[feature][0]
            continue
        low, _high = FEATURE_BOUNDS[feature]
        body[feature] = (
            int(low) if feature in INTEGER_FEATURES + FLAG_FEATURES else float(low)
        )
    return body


_SCORING_REQUESTS = [
    ("GET", "/students", None),
    ("GET", "/predict/STU300001", None),
    ("POST", "/predict", _minimal_predict_body()),
]


def test_health_reports_every_component(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["components"] == {
        "model": True,
        "explainer": True,
        "calibrator": True,
        "meta": True,
    }


@pytest.mark.parametrize("component", ["model", "explainer", "calibrator", "meta"])
def test_a_missing_component_is_degraded_and_scoring_is_503(client, component, monkeypatch):
    """Including the calibrator: without it `churn_proba` returns CatBoost's raw
    score, so the service would keep answering with numbers that are not
    probabilities while the threshold was chosen on calibrated ones."""
    monkeypatch.setattr(client.app.state, component, {} if component == "meta" else None)

    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert health["components"][component] is False
    # Still no file path in the body of the one unauthenticated endpoint.
    assert "reason" not in health

    for method, path, payload in _SCORING_REQUESTS:
        response = client.request(method, path, json=payload)
        assert response.status_code == 503, (method, path, response.json())
        assert component in response.json()["detail"]


def test_loading_a_missing_calibrator_raises(tmp_path):
    with pytest.raises(CalibratorMissingError):
        load_calibrator(str(tmp_path / "no-such-calibrator.joblib"))


def test_a_caller_can_ask_for_raw_scores_on_purpose(tmp_path):
    assert load_calibrator(str(tmp_path / "no-such-calibrator.joblib"), required=False) is None


def test_the_committed_calibrator_still_loads():
    assert load_calibrator(CALIBRATOR_PATH) is not None


def test_meta_must_match_the_served_feature_list():
    meta = load_meta(MODEL_META_PATH)
    check_meta_matches_config(meta)  # the committed pair agrees

    reordered = {**meta, "features": list(reversed(meta["features"]))}
    with pytest.raises(RuntimeError, match="features does not match config"):
        check_meta_matches_config(reordered)

    with pytest.raises(RuntimeError, match="cat_cols does not match config"):
        check_meta_matches_config({**meta, "cat_cols": ["grade"]})

    with pytest.raises(RuntimeError, match="no 'features'"):
        check_meta_matches_config({k: v for k, v in meta.items() if k != "features"})

    with pytest.raises(RuntimeError, match="missing or empty"):
        check_meta_matches_config({})
