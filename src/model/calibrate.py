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


class DegenerateCalibrationError(ValueError):
    """The calibration set holds one class, so there is no curve to fit (B-26).

    This used to be swallowed: a single-class validation set produced a CONSTANT
    calibrator, the training run finished, and the meta file it wrote looked
    entirely reasonable - chosen threshold 0.01, ROC-AUC 0.5, a calibrator that
    returns the base rate for every student. The pipeline then flagged nobody,
    every day, without a single error. On a pilot with few churners that is the
    most expensive failure mode in the system, so it now stops the run.

    A caller that genuinely wants the base-rate constant (a smoke test, a
    deliberate degraded run) passes `allow_single_class=True` and gets a
    calibrator whose `.degraded` is True, which the meta records.
    """


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
        # True only on the opt-in single-class path: a calibrator that carries no
        # information about the model's scores. The meta file publishes it so a
        # degraded run is visible in model_meta.json rather than only in a log line.
        self.degraded = False

    def fit(self, raw, y, *, allow_single_class: bool = False):
        raw = np.asarray(raw, dtype=float).reshape(-1, 1)
        y = np.asarray(y, dtype=int)
        # One class: there is no curve to fit. Failing here is the point - see
        # DegenerateCalibrationError for what the silent constant used to cost.
        if np.unique(y).size < 2:
            if not allow_single_class:
                raise DegenerateCalibrationError(_single_class_message(y))
            logger.error(
                "calibrating on a single-class set: the calibrator is the constant "
                "%.4f and carries no information - this run is DEGRADED",
                float(y.mean()),
            )
            self._constant = float(y.mean())
            self.degraded = True
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


def _single_class_message(y) -> str:
    classes = sorted({int(v) for v in np.asarray(y, dtype=int).tolist()})
    return (
        f"cannot calibrate on {len(y)} rows holding only class {classes}: a "
        "single-class calibration set produces a constant calibrator, which scores "
        "every student identically and makes the whole pipeline silently useless. "
        "Use a split that contains both classes (see src/data/preprocess.py), or "
        "pass allow_single_class=True to accept a DEGRADED calibrator on purpose."
    )


def is_degraded(calibrator) -> bool:
    """True when this calibrator is the single-class constant (B-26).

    `getattr` rather than an attribute access: a calibrator unpickled from a file
    written before B-26, and an IsotonicRegression that was never marked, both
    predate the flag and are not degraded as far as anything here can tell.
    """
    return bool(getattr(calibrator, "degraded", False))


def fit_calibrator(model, X_val, y_val, *, method: str | None = None,
                   allow_single_class: bool = False):
    """Fit the configured calibrator: raw P(churn) -> calibrated P(churn).

    Raises DegenerateCalibrationError when `y_val` holds a single class, unless
    `allow_single_class=True` (then the calibrator comes back with `.degraded`
    True). The check lives here, not only in PlattCalibrator, because isotonic
    regression collapses to a constant on single-class data just as quietly.
    """
    y = np.asarray(y_val, dtype=float)
    single_class = np.unique(y).size < 2
    if single_class and not allow_single_class:
        raise DegenerateCalibrationError(_single_class_message(y))

    calibrator = build_calibrator(method)
    raw = raw_churn_proba(model, X_val)
    if isinstance(calibrator, PlattCalibrator):
        calibrator.fit(raw, y, allow_single_class=allow_single_class)
    else:
        calibrator.fit(raw, y)
        if single_class:
            logger.error(
                "calibrating on a single-class set: the isotonic calibrator is a "
                "constant and carries no information - this run is DEGRADED"
            )
            calibrator.degraded = True
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
