"""Calibration must be monotonic (it only re-labels probabilities, never re-ranks
students) and must not make the Brier score worse."""
import numpy as np
from sklearn.metrics import brier_score_loss

from src.model.calibrate import churn_proba


class _FakeModel:
    """Returns a fixed raw P(churn) column, so we can test calibration in isolation."""

    def __init__(self, raw):
        self._raw = np.asarray(raw, dtype=float)

    def predict_proba(self, X):
        return np.column_stack([1 - self._raw, self._raw])


def _fit_isotonic(raw, y):
    from src.model.calibrate import fit_calibrator

    return fit_calibrator(_FakeModel(raw), None, y)


def test_calibration_is_monotonic():
    rng = np.random.default_rng(0)
    raw = rng.random(400)
    y = (rng.random(400) < raw * 0.6 + 0.1).astype(int)

    calibrator = _fit_isotonic(raw, y)
    order = np.argsort(raw)
    calibrated = calibrator.predict(raw[order])
    assert np.all(np.diff(calibrated) >= -1e-9)  # never decreases as raw increases


def test_calibration_does_not_worsen_brier():
    rng = np.random.default_rng(1)
    raw = np.clip(rng.random(600) ** 2 + 0.1, 0, 1)  # deliberately miscalibrated
    y = (rng.random(600) < raw).astype(int)

    calibrator = _fit_isotonic(raw, y)
    model = _FakeModel(raw)
    calibrated = churn_proba(model, X=None, calibrator=calibrator)

    assert brier_score_loss(y, calibrated) <= brier_score_loss(y, raw) + 1e-9


def test_no_calibrator_returns_raw():
    model = _FakeModel([0.1, 0.5, 0.9])
    assert np.allclose(churn_proba(model, X=None, calibrator=None), [0.1, 0.5, 0.9])
