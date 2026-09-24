"""scripts/seed_demo_history.py - the demo's synthetic "yesterday" run.

The point of the script is that the *next* real run shows both halves of the
daily message: a `new` section and a `still_at_risk` section with trend arrows.
These tests pin that outcome end to end, plus the invariants of the pure part.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from config import CALIBRATOR_PATH, MODEL_META_PATH, MODEL_PATH
from pipeline.daily_pipeline import log_alerts, score_students
from src.data.loader import append_to_alert_log, previous_run_probabilities
from src.explainer.shap_explainer import create_explainer
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model
from src.notifications.message import build_message

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_history.py"
_spec = importlib.util.spec_from_file_location("seed_demo_history", _PATH)
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)

THRESHOLD = 0.29


def _at_risk(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "student_id": [f"S{i:03d}" for i in range(n)],
            "churn_probability": [0.30 + 0.05 * i for i in range(n)],
            "top_reasons": ["x (+0.10)"] * n,
        }
    )


# --- the pure part ------------------------------------------------------------
def test_leaves_at_least_one_new_student():
    for n in (2, 3, 5, 8, 20):
        history = seed.make_history(_at_risk(n), THRESHOLD)
        assert 1 <= len(history) <= n - 1, n


def test_is_deterministic():
    a = seed.make_history(_at_risk(10), THRESHOLD)
    b = seed.make_history(_at_risk(10), THRESHOLD)
    pd.testing.assert_frame_equal(a, b)


def test_does_not_depend_on_row_order():
    df = _at_risk(10)
    a = seed.make_history(df, THRESHOLD)
    b = seed.make_history(df.iloc[::-1].reset_index(drop=True), THRESHOLD)
    assert set(a["student_id"]) == set(b["student_id"])


def test_yesterday_probabilities_are_plausible():
    history = seed.make_history(_at_risk(20), THRESHOLD)
    # Anyone recorded yesterday must have been at or above the threshold then,
    # otherwise they would not be in the alert log at all.
    assert (history["churn_probability"] >= THRESHOLD).all()
    assert (history["churn_probability"] <= seed.CEILING).all()
    assert (history["status"] == "new").all()


def test_too_few_students_seeds_nothing():
    assert seed.make_history(_at_risk(0), THRESHOLD).empty
    assert seed.make_history(_at_risk(1), THRESHOLD).empty


def test_input_is_not_mutated():
    df = _at_risk(8)
    before = df.copy()
    seed.make_history(df, THRESHOLD)
    pd.testing.assert_frame_equal(df, before)


# --- the outcome the script exists for ---------------------------------------
@pytest.fixture(scope="module")
def scoring_kwargs():
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH)
    return dict(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta["imputation_values"],
        threshold=meta["chosen_threshold"],
    )


def test_next_real_run_shows_both_sections_and_trends(tmp_path, scoring_kwargs):
    alerts_path = str(tmp_path / "daily_alerts.csv")

    today = score_students(**scoring_kwargs)
    assert len(today) >= 2, "the sample must have at least two at-risk students"

    history = seed.make_history(today, scoring_kwargs["threshold"])
    append_to_alert_log(
        history, alerts_path, run_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    marked = log_alerts(today, alerts_path)

    statuses = set(marked["status"])
    assert statuses == {"new", "still_at_risk"}
    assert (marked["status"] == "still_at_risk").sum() == len(history)

    previous = previous_run_probabilities(alerts_path)
    assert set(previous) == set(history["student_id"])

    new = marked[marked["status"] == "new"]
    still = marked[marked["status"] == "still_at_risk"]
    _, text = build_message(new, still_at_risk=still, previous_probabilities=previous)
    assert "önceki %" in text  # the trend arrows actually render
