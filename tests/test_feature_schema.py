"""GET /schema is a contract, so these tests pin the promises consumers rely on.

Two services outside this repo (the dashboard and the retention app) hold their
own tables keyed by feature name. The point of this endpoint is that they can
stop guessing; the point of these tests is that the endpoint cannot quietly
change shape under them.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from api.main import app
from config import API_KEY, CAT_COLS, FEATURES, FLAG_FEATURES, STUDENT_INFO
from src.feature_schema import feature_schema, feature_set_hash


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"X-API-Key": API_KEY}, raise_server_exceptions=False) as c:
        yield c


def test_every_feature_appears_exactly_once_and_in_order():
    schema = feature_schema()
    names = [item["name"] for item in schema["features"]]
    assert names == FEATURES


def test_hash_is_order_independent_but_membership_sensitive(monkeypatch):
    import src.feature_schema as module

    original = feature_set_hash()

    monkeypatch.setattr(module, "FEATURES", list(reversed(FEATURES)))
    assert feature_set_hash() == original, "reordering is not a contract change"

    monkeypatch.setattr(module, "FEATURES", FEATURES[:-1])
    assert feature_set_hash() != original, "dropping a column must change the hash"


def test_hash_matches_a_hand_computed_sha256():
    expected = hashlib.sha256("\n".join(sorted(FEATURES)).encode("utf-8")).hexdigest()
    assert feature_set_hash() == expected


def test_categoricals_publish_their_levels():
    schema = feature_schema()
    for item in schema["features"]:
        if item["name"] in CAT_COLS:
            assert item["type"] == "categorical"
            assert item["levels"], f"{item['name']} has no levels"
            assert "min" not in item and "max" not in item
        else:
            assert "levels" not in item


def test_flags_are_marked_so_a_consumer_cannot_show_them_as_measurements():
    schema = feature_schema()
    flagged = {item["name"] for item in schema["features"] if item["is_flag"]}
    assert flagged == set(FLAG_FEATURES)


def test_numeric_features_publish_their_accepted_range():
    schema = feature_schema()
    numeric = [item for item in schema["features"] if item["type"] == "number"]
    assert numeric
    for item in numeric:
        assert "integer" in item
        if "min" in item:
            assert item["min"] <= item["max"]


def test_info_fields_are_published_and_are_not_features():
    schema = feature_schema()
    assert schema["info_fields"] == list(STUDENT_INFO)
    assert schema["id_field"] == STUDENT_INFO[0]
    assert not set(schema["info_fields"]) & set(FEATURES)


def test_schema_carries_no_training_statistics():
    """The error breakdown, imputation medians, paths and data hash stay out."""
    import json

    body = json.dumps(feature_schema())
    for leaked in ("imputation", "sha256", "error_analysis", "model_params", "/"):
        assert leaked not in body, f"{leaked!r} must not be in the public contract"


def test_endpoint_answers_without_a_loaded_model(client):
    """A consumer checks the contract at startup, before anything is trained.

    Restored afterwards: the client is module-scoped, so leaving `meta` as None
    would make every later test in this file read a half-initialised app.
    """
    saved = client.app.state.meta
    client.app.state.meta = None
    try:
        response = client.get("/schema")
        assert response.status_code == 200
        assert response.json()["feature_set_hash"] == feature_set_hash()
    finally:
        client.app.state.meta = saved


def test_endpoint_body_matches_the_module(client):
    assert client.get("/schema").json() == feature_schema()
