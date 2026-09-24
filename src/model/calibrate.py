"""Turn the model's raw scores into probabilities that mean what they say.

With `auto_class_weights="Balanced"`, CatBoost's raw `predict_proba` is not a real
probability - 0.5 is an artefact of the class weighting. `fit_calibrator` learns a
monotonic mapping from raw score to observed churn rate on the validation set, so
the number the API returns as `churn_probability` is one a mentor can read as
"this student's chance of leaving".

Two methods, chosen by `config.CALIBRATION_METHOD` (see the comment there for the
trade-off). Both expose `.predict()`, so nothing downstream knows which is in use.
"""
import logging
import os

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from config import CALIBRATION_METHOD

logger = logging.getLogger(__name__)

METHODS = ("sigmoid", "isotonic")


class CalibratorMissingError(RuntimeError):
    """`CALIBRATION_METHOD` asks for a calibrator and there is no file to load."""


class PlattCalibrator:
    """Platt scaling: a logistic curve fitted on the raw score.

    Strictly increasing, so it never produces a tie that was not already there -
    which is the whole reason it is the default. A module-level class rather than a
    closure so joblib can pickle it.
    """

    def __init__(self):
        # Effectively unregularised: this is a two-parameter fit on one feature, and
        # shrinking it towards zero would flatten the very curve we are fitting.
        self._model = LogisticRegression(C=1e10, solver="lbfgs")
        self._constant = None

    def fit(self, raw, y):
        raw = np.asarray(raw, dtype=float).reshape(-1, 1)
        y = np.asarray(y, dtype=int)
        # One class in the validation set: there is no curve to fit, and the honest
        # answer is the base rate. Rare, but it must not raise in a training run.
        if np.unique(y).size < 2:
            self._constant = float(y.mean())
            return self
        self._model.fit(raw, y)
        return self

    def predict(self, raw):
        raw = np.asarray(raw, dtype=float).reshape(-1, 1)
        if self._constant is not None:
            return np.full(raw.shape[0], self._constant)
        return self._model.predict_proba(raw)[:, 1]


def raw_churn_proba(model, X):
    """The model's uncalibrated P(churn)."""
    return model.predict_proba(X)[:, 1]


def build_calibrator(method: str | None = None):
    """An unfitted calibrator for `method` (default: config.CALIBRATION_METHOD)."""
    method = (method or CALIBRATION_METHOD).lower()
    if method not in METHODS:
        raise ValueError(f"unknown CALIBRATION_METHOD {method!r}; known: {list(METHODS)}")
    return PlattCalibrator() if method == "sigmoid" else IsotonicRegression(out_of_bounds="clip")


def fit_calibrator(model, X_val, y_val, *, method: str | None = None):
    """Fit the configured calibrator: raw P(churn) -> calibrated P(churn)."""
    calibrator = build_calibrator(method)
    calibrator.fit(raw_churn_proba(model, X_val), np.asarray(y_val, dtype=float))
    return calibrator


def churn_proba(model, X, calibrator=None):
    """Calibrated P(churn) when a calibrator is given, otherwise the raw value."""
    raw = raw_churn_proba(model, X)
    return raw if calibrator is None else calibrator.predict(raw)


def save_calibrator(calibrator, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(calibrator, path)


def load_calibrator(path, *, required: bool = True):
    """Load the saved calibrator. Raise if it is missing and one is configured.

    Swallowing the missing file and returning None was the quietest failure in the
    system: `churn_proba` then hands back CatBoost's raw score, which with
    `auto_class_weights="Balanced"` is not a probability (Brier 0.203 raw vs 0.173
    calibrated) - while the alert threshold, 0.29, was chosen on calibrated scores.
    The mentor's list grows several times over, every probability shown is wrong, and
    nothing anywhere says so. Failing to start is the better outcome.

    `required=False` is for a caller that deliberately wants the raw score (a
    diagnostic, a comparison), not for serving.
    """
    try:
        return joblib.load(path)
    except (FileNotFoundError, OSError) as e:
        if required and CALIBRATION_METHOD:
            raise CalibratorMissingError(
                f"CALIBRATION_METHOD is {CALIBRATION_METHOD!r} but no calibrator could be "
                f"loaded from {path}: {e}. Run running_train_pipeline.py, or set "
                f"CALIBRATION_METHOD to a falsy value to serve raw scores on purpose."
            )
        logger.warning("no calibrator loaded from %s - probabilities will be RAW", path)
        return None
