"""Calibration must be monotonic (it only re-labels probabilities, never re-ranks
students), must not make the Brier score worse, and - for the configured default -
must not throw away the ranking resolution the model produced."""
import numpy as np
import pytest
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.model.calibrate import METHODS, build_calibrator, churn_proba, fit_calibrator


class _FakeModel:
    """Returns a fixed raw P(churn) column, so we can test calibration in isolation."""

    def __init__(self, raw):
        self._raw = np.asarray(raw, dtype=float)

    def predict_proba(self, X):
        return np.column_stack([1 - self._raw, self._raw])


def _fit(raw, y, method=None):
    return fit_calibrator(_FakeModel(raw), None, y, method=method)


@pytest.mark.parametrize("method", METHODS)
def test_calibration_is_monotonic(method):
    rng = np.random.default_rng(0)
    raw = rng.random(400)
    y = (rng.random(400) < raw * 0.6 + 0.1).astype(int)

    calibrator = _fit(raw, y, method)
    order = np.argsort(raw)
    calibrated = calibrator.predict(raw[order])
    assert np.all(np.diff(calibrated) >= -1e-9)  # never decreases as raw increases


@pytest.mark.parametrize("method", METHODS)
def test_calibration_fixes_a_badly_scaled_score(method):
    """The situation calibration exists for, and the one the old version of this
    test did not actually create: `raw` here is monotone in the truth but on the
    wrong scale - squashed towards the middle, exactly what balanced class weights
    do to CatBoost's output. Both methods must recover the scale.

    Note what is NOT asserted: that calibration always helps. Given a score that is
    already a true probability, refitting it can only add variance, and sigmoid -
    two parameters against isotonic's arbitrary step function - loses that contest.
    The training summary reports both Brier scores for exactly this reason.
    """
    rng = np.random.default_rng(1)
    truth = rng.random(600)
    y = (rng.random(600) < truth).astype(int)
    raw = 0.25 + truth * 0.5  # right order, wrong scale

    calibrator = _fit(raw, y, method)
    calibrated = churn_proba(_FakeModel(raw), X=None, calibrator=calibrator)

    assert brier_score_loss(y, calibrated) < brier_score_loss(y, raw)


def test_sigmoid_keeps_every_student_distinguishable():
    """The reason sigmoid is the default.

    Isotonic maps whole intervals onto one value; on this project's data it turned
    677 distinct test scores into 24, which is what put four of eight daily at-risk
    students on the identical probability. A mentor with time for three calls cannot
    act on that. Platt scaling is strictly increasing, so the ranking survives.
    """
    rng = np.random.default_rng(2)
    raw = rng.random(300)
    y = (rng.random(300) < raw).astype(int)

    sigmoid = _fit(raw, y, "sigmoid").predict(raw)
    isotonic = _fit(raw, y, "isotonic").predict(raw)

    assert np.unique(sigmoid).size == np.unique(raw).size
    assert np.unique(isotonic).size < np.unique(raw).size
    # No ties means no lost ranking: AUC is preserved exactly.
    assert roc_auc_score(y, sigmoid) == pytest.approx(roc_auc_score(y, raw))


def test_single_class_validation_set_falls_back_to_the_base_rate():
    """A degenerate validation split must not kill a training run."""
    calibrated = _fit(np.linspace(0, 1, 50), np.zeros(50, dtype=int), "sigmoid").predict(
        np.array([0.1, 0.9])
    )
    assert np.allclose(calibrated, 0.0)


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError):
        build_calibrator("bayesian-vibes")


def test_no_calibrator_returns_raw():
    model = _FakeModel([0.1, 0.5, 0.9])
    assert np.allclose(churn_proba(model, X=None, calibrator=None), [0.1, 0.5, 0.9])
