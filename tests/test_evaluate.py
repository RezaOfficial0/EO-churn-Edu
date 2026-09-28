"""The reported metrics have to mean what their names say (B-26).

These numbers go into model_meta.json, out of GET /metrics and into a pitch deck,
so the tests here are less about arithmetic than about labelling: precision@k named
for the k that was actually measured, the calibrated/uncalibrated pair only present
when there is something to compare, and the whole dict JSON-serialisable.
"""
import json
import logging

import numpy as np
import pandas as pd
import pytest

from src.model.evaluate import (
    effective_k,
    evaluate_model,
    false_negative_breakdown,
    lift_at_k,
    precision_at_k,
)


# --- precision@k / lift@k -----------------------------------------------------


def test_precision_at_k_counts_the_churners_among_the_top_k():
    # Ranked by score: 0.9(churn) 0.8(churn) 0.7(no) 0.6(churn) 0.5(no) ...
    y = [1, 1, 0, 1, 0, 0, 0, 0]
    proba = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]

    assert precision_at_k(y, proba, 1) == 1.0
    assert precision_at_k(y, proba, 3) == pytest.approx(2 / 3)
    assert precision_at_k(y, proba, 4) == 0.75
    assert precision_at_k(y, proba, 8) == 0.375  # == the base rate at k == n


def test_precision_at_k_ignores_the_row_order_and_uses_the_score():
    """The input frame is in student_id order, never in risk order."""
    y = [0, 0, 1, 1]
    proba = [0.1, 0.2, 0.95, 0.9]

    assert precision_at_k(y, proba, 2) == 1.0


def test_k_larger_than_the_test_set_is_reported_at_the_effective_k(caplog):
    """The B-26 defect: 12 rows, k=20. The average over 12 is a fine number to
    report - calling it precision@20 is not, because it claims the model was
    measured at a mentor capacity the test set could never fill."""
    y = [1] * 6 + [0] * 6
    proba = list(np.linspace(0.9, 0.1, 12))

    assert effective_k(y, 20) == 12
    with caplog.at_level(logging.WARNING):
        value = precision_at_k(y, proba, 20)

    assert value == 0.5  # the 12 rows it had, unchanged
    assert "precision@12" in caplog.text  # ... and it says so


def test_the_metric_key_is_named_for_the_k_that_was_measured():
    y = [1] * 6 + [0] * 6
    proba = list(np.linspace(0.9, 0.1, 12))

    metrics = evaluate_model(y, proba, threshold=0.5, k=20)

    assert "precision_at_20" not in metrics  # the lie this issue names
    assert "lift_at_20" not in metrics
    assert metrics["precision_at_12"] == 0.5
    assert metrics["precision_at_k_requested"] == 20
    assert metrics["precision_at_k_effective"] == 12


def test_the_key_keeps_the_requested_k_when_the_test_set_is_big_enough():
    """The normal case must not be renamed: PRECISION_AT_K is 20 and every existing
    meta file, dashboard label and README number says precision_at_20."""
    rng = np.random.default_rng(0)
    proba = rng.random(100)
    y = (rng.random(100) < proba).astype(int)

    metrics = evaluate_model(y, proba, threshold=0.5, k=20)

    assert "precision_at_20" in metrics and "lift_at_20" in metrics
    assert metrics["precision_at_k_requested"] == 20
    assert metrics["precision_at_k_effective"] == 20


def test_k_of_zero_is_zero_rather_than_an_error():
    assert precision_at_k([1, 0], [0.9, 0.1], 0) == 0.0
    assert lift_at_k([1, 0], [0.9, 0.1], 0) == 0.0
    assert effective_k([1, 0], None) == 0


def test_precision_at_k_on_an_empty_test_set_is_zero():
    assert precision_at_k([], [], 20) == 0.0
    assert effective_k([], 20) == 0


def test_tied_scores_are_broken_by_reverse_row_order():
    """Documents an artefact, not a decision.

    With every score identical, `np.argsort(...)[::-1]` returns the LAST k rows, so
    precision@k on a tied block depends on the frame's order. That matters here:
    isotonic calibration deliberately creates ties (config.CALIBRATION_METHOD), and
    a switch to `argsort(-proba)` would silently pick the FIRST k instead and move
    this number. Pinned so the change is visible; nothing here says the current
    tie-break is the right one.
    """
    assert precision_at_k([1, 1, 0, 0], [0.5] * 4, 2) == 0.0
    assert precision_at_k([0, 0, 1, 1], [0.5] * 4, 2) == 1.0


def test_lift_at_k_is_precision_at_k_over_the_base_rate():
    y = [1, 1, 0, 1, 0, 0, 0, 0]
    proba = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]

    assert lift_at_k(y, proba, 4) == pytest.approx(0.75 / 0.375)
    # k == n is the base rate itself, so no model can lift it.
    assert lift_at_k(y, proba, 8) == pytest.approx(1.0)


def test_lift_at_k_with_no_churners_is_zero_not_a_division_by_zero():
    assert lift_at_k([0, 0, 0], [0.9, 0.5, 0.1], 2) == 0.0


# --- evaluate_model -----------------------------------------------------------


def _perfect():
    y = np.array([1, 1, 1, 0, 0, 0])
    proba = np.array([0.9, 0.8, 0.7, 0.2, 0.1, 0.05])
    return y, proba


def test_evaluate_model_scores_a_perfect_ranking_perfectly():
    y, proba = _perfect()
    metrics = evaluate_model(y, proba, threshold=0.5, k=3)

    assert metrics["roc_auc"] == 1.0
    assert metrics["average_precision"] == 1.0
    assert (metrics["precision"], metrics["recall"], metrics["f1"]) == (1.0, 1.0, 1.0)
    assert metrics["precision_at_3"] == 1.0
    assert metrics["confusion_matrix"] == [[3, 0], [0, 3]]


def test_the_threshold_is_applied_with_greater_or_equal():
    """Same rule as select_threshold, and the API's at-risk list. A student sitting
    exactly on the cut is predicted at risk."""
    metrics = evaluate_model([1, 0], [0.30, 0.10], threshold=0.30, k=1)

    assert metrics["recall"] == 1.0
    assert metrics["confusion_matrix"] == [[1, 0], [0, 1]]


def test_a_threshold_nobody_reaches_reports_zero_instead_of_dividing_by_zero():
    metrics = evaluate_model([1, 0, 1], [0.1, 0.2, 0.3], threshold=0.9, k=2)

    assert (metrics["precision"], metrics["recall"], metrics["f1"]) == (0.0, 0.0, 0.0)


def test_the_brier_score_measures_the_probabilities_not_the_ranking():
    """Two scores with the same perfect ranking and different calibration: the
    ranking metrics cannot tell them apart and Brier must."""
    y, proba = _perfect()
    squashed = 0.45 + (proba - 0.5) * 0.1  # same order, pressed into the middle

    honest = evaluate_model(y, proba, threshold=0.5, k=3)
    squashed_metrics = evaluate_model(y, squashed, threshold=0.5, k=3)

    assert squashed_metrics["roc_auc"] == honest["roc_auc"]
    assert squashed_metrics["brier_score"] > honest["brier_score"]


def test_the_uncalibrated_comparison_appears_only_when_a_raw_score_is_given():
    y, proba = _perfect()
    without = evaluate_model(y, proba, threshold=0.5, k=3)
    assert not [key for key in without if key.endswith("_uncalibrated")]
    assert "distinct_scores" not in without

    raw = np.array([0.55, 0.54, 0.53, 0.46, 0.45, 0.44])
    with_raw = evaluate_model(y, proba, threshold=0.5, k=3, raw_churn_proba=raw)
    assert with_raw["brier_score_uncalibrated"] > with_raw["brier_score"]
    assert with_raw["roc_auc_uncalibrated"] == with_raw["roc_auc"]
    assert with_raw["average_precision_uncalibrated"] == with_raw["average_precision"]


def test_distinct_scores_counts_the_ties_calibration_created():
    """The number that caught isotonic collapsing 677 test scores onto 24 - it has
    to count unique values on both sides, not rows."""
    y = np.array([1, 1, 0, 0])
    raw = np.array([0.9, 0.8, 0.2, 0.1])
    tied = np.array([0.9, 0.9, 0.1, 0.1])

    metrics = evaluate_model(y, tied, threshold=0.5, k=2, raw_churn_proba=raw)

    assert metrics["distinct_scores"] == 2
    assert metrics["distinct_scores_uncalibrated"] == 4


def test_every_metric_is_a_json_type():
    """model_meta.json is written with plain `json.dump`: one numpy float in this
    dict and the training run dies after the model has been fitted."""
    y, proba = _perfect()
    metrics = evaluate_model(y, proba, threshold=0.5, k=20, raw_churn_proba=proba)

    assert json.loads(json.dumps(metrics)) == metrics
    assert isinstance(metrics["threshold"], float)
    assert isinstance(metrics["precision_at_k_effective"], int)


def test_evaluate_model_accepts_a_series_with_a_shuffled_index():
    """`y_test` comes out of a stratified split, never as a 0..n-1 array."""
    y, proba = _perfect()
    series = pd.Series(y, index=[9, 4, 77, 2, 31, 5])

    assert evaluate_model(series, proba, threshold=0.5, k=3) == evaluate_model(
        y, proba, threshold=0.5, k=3
    )


# --- false_negative_breakdown -------------------------------------------------


def _breakdown_frame():
    return pd.DataFrame(
        {
            "plan_type": ["Aylık", "Aylık", "Aylık", "Yıllık", "Yıllık"],
            "churn": [1, 1, 0, 1, 0],
            "proba": [0.9, 0.1, 0.9, 0.1, 0.1],
        }
    )


def test_false_negative_breakdown_counts_missed_churners_per_group():
    frame = _breakdown_frame()
    breakdown = false_negative_breakdown(
        frame[["plan_type"]], frame["churn"], frame["proba"], 0.5, ["plan_type"]
    )

    # Aylık: 2 churners, the 0.1 one is missed. Yıllık: 1 churner, missed.
    assert breakdown == {
        "plan_type": {
            "Aylık": {"churners": 2, "missed": 1},
            "Yıllık": {"churners": 1, "missed": 1},
        }
    }


def test_false_negative_breakdown_ignores_non_churners_entirely():
    """It answers 'which students does it miss?', so a false ALARM must not appear
    in it - the 0.9-scoring non-churner above is in neither count."""
    frame = _breakdown_frame()
    breakdown = false_negative_breakdown(
        frame[["plan_type"]], frame["churn"], frame["proba"], 0.5, ["plan_type"]
    )

    assert sum(g["churners"] for g in breakdown["plan_type"].values()) == 3


def test_false_negative_breakdown_does_not_mutate_the_test_frame():
    """It adds `_actual` / `_predicted` columns; X_test is reused afterwards for the
    baselines, so those must not leak into it."""
    frame = _breakdown_frame()
    X_test = frame[["plan_type"]]

    false_negative_breakdown(X_test, frame["churn"], frame["proba"], 0.5, ["plan_type"])

    assert list(X_test.columns) == ["plan_type"]


def test_false_negative_breakdown_keys_are_strings_so_it_serialises():
    """Grouping on a numeric column gives numpy keys, which json.dump rejects."""
    frame = pd.DataFrame({"grade": [11, 11, 12], "churn": [1, 1, 1], "p": [0.9, 0.1, 0.1]})
    breakdown = false_negative_breakdown(
        frame[["grade"]], frame["churn"], frame["p"], 0.5, ["grade"]
    )

    assert set(breakdown["grade"]) == {"11", "12"}
    assert json.loads(json.dumps(breakdown)) == breakdown
