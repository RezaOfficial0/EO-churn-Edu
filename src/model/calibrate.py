"""Isotonic calibration of the model's probabilities.

With `auto_class_weights="Balanced"`, CatBoost's raw `predict_proba` is not a real
probability - 0.5 is an artefact of the class weighting. `fit_calibrator` learns a
monotonic mapping from raw probability to observed churn rate on the validation set,
so the number the API returns as `churn_probability` means what it says.
"""
import os

import joblib
import numpy as np
from sklearn.isotonic import IsotonicRegression


def raw_churn_proba(model, X):
    """The model's uncalibrated P(churn)."""
    return model.predict_proba(X)[:, 1]


def fit_calibrator(model, X_val, y_val):
    """Fit isotonic regression mapping raw P(churn) -> calibrated P(churn)."""
    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(raw_churn_proba(model, X_val), np.asarray(y_val, dtype=float))
    return calibrator


def churn_proba(model, X, calibrator=None):
    """Calibrated P(churn) when a calibrator is given, otherwise the raw value."""
    raw = raw_churn_proba(model, X)
    return raw if calibrator is None else calibrator.predict(raw)


def save_calibrator(calibrator, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(calibrator, path)


def load_calibrator(path):
    """Load a saved calibrator, or return None if the file is missing."""
    try:
        return joblib.load(path)
    except (FileNotFoundError, OSError):
        return None
