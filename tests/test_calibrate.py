"""Calibration must be monotonic (it only re-labels probabilities, never re-ranks
students), must not make the Brier score worse, and - for the configured default -
must not throw away the ranking resolution the model produced."""
import numpy as np
import pytest
from sklearn.metrics import brier_score_loss, roc_auc_score

from src.model.calibrate import (
    METHODS,
    DegenerateCalibrationError,
    PlattCalibrator,
    build_calibrator,
    churn_proba,
    fit_calibrator,
    is_degraded,
    load_calibrator,
    save_calibrator,
)


class _FakeModel:
    """Returns a fixed raw P(churn) column, so we can test calibration in isolation."""

    def __init__(self, raw):
        self._raw = np.asarray(raw, dtype=float)

    def predict_proba(self, X):
        return np.column_stack([1 - self._raw, self._raw])


def _fit(raw, y, method=None):
    return fit_calibrator(_FakeModel(raw), None, y, method=method)


def _sigmoid(z):
    """Written out rather than imported, so the mapping this test pins is visible in
    the test itself: sigma(a*f + b) applied to whatever `f` the calibrator fitted on."""
    return 1.0 / (1.0 + np.exp(-z))


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


# --- single-class calibration set (B-26) --------------------------------------
#
# This replaces a test that asserted the opposite ("falls back to the base rate").
# That test encoded the bug named in B-26: the constant calibrator it accepted is
# what let a training run finish, write a plausible-looking model_meta.json, and
# then flag nobody for as long as it was served. The behaviour is now loud, so the
# test that blessed the silence had to go with it.


@pytest.mark.parametrize("method", METHODS)
def test_single_class_calibration_set_raises(method):
    with pytest.raises(DegenerateCalibrationError) as excinfo:
        _fit(np.linspace(0, 1, 50), np.zeros(50, dtype=int), method)
    # The message has to tell whoever reads the failing training log what to do.
    assert "allow_single_class" in str(excinfo.value)


@pytest.mark.parametrize("method", METHODS)
def test_single_class_is_only_accepted_on_request_and_is_marked_degraded(method):
    """The escape hatch: a constant calibrator is available, never by accident."""
    calibrator = fit_calibrator(
        _FakeModel(np.linspace(0, 1, 50)),
        None,
        np.zeros(50, dtype=int),
        method=method,
        allow_single_class=True,
    )
    assert is_degraded(calibrator) is True
    assert np.allclose(calibrator.predict(np.array([0.1, 0.9])), 0.0)


def test_the_platt_constant_is_the_base_rate_of_the_single_class():
    calibrator = PlattCalibrator().fit(
        np.linspace(0, 1, 20), np.ones(20, dtype=int), allow_single_class=True
    )
    assert np.allclose(calibrator.predict(np.array([0.0, 0.5, 1.0])), 1.0)


@pytest.mark.parametrize("method", METHODS)
def test_a_healthy_calibrator_is_not_degraded(method):
    rng = np.random.default_rng(3)
    raw = rng.random(200)
    assert is_degraded(_fit(raw, (rng.random(200) < raw).astype(int), method)) is False


def test_is_degraded_is_false_for_a_calibrator_that_predates_the_flag():
    """An object unpickled from a calibrator.joblib written before B-26 has no
    `degraded` attribute at all - it must not blow up `is_degraded`, and it is not
    evidence of a degraded run either way."""
    class _Old:
        def predict(self, raw):
            return raw

    assert is_degraded(_Old()) is False


# --- what B-27 will change ---------------------------------------------------


def test_platt_currently_fits_on_the_probability_scale_not_log_odds():
    """Pins the deficiency B-27 is about, so the switch is a visible change.

    Platt scaling fits sigma(a*f + b) on a DECISION FUNCTION; this code fits it on
    `predict_proba[:, 1]`, which is already squashed into [0, 1]. The consequence
    asserted here is exact: `predict` is the logistic of a straight line in the RAW
    PROBABILITY, so it reproduces sklearn's own coefficients applied to `raw`.

    When B-27 moves the fit onto log(p/(1-p)), this test MUST fail and be rewritten
    to assert the log-odds form - that is what it is here for. Nothing about it says
    the current scale is correct.
    """
    rng = np.random.default_rng(4)
    raw = rng.random(300)
    y = (rng.random(300) < raw).astype(int)

    calibrator = _fit(raw, y, "sigmoid")
    coefficient = float(calibrator._model.coef_[0][0])
    intercept = float(calibrator._model.intercept_[0])

    probe = np.array([0.05, 0.3, 0.5, 0.7, 0.95])
    assert np.allclose(calibrator.predict(probe), _sigmoid(coefficient * probe + intercept))
    # ... and not the log-odds fit, which is a genuinely different mapping here.
    log_odds = np.log(probe / (1 - probe))
    assert not np.allclose(
        calibrator.predict(probe), _sigmoid(coefficient * log_odds + intercept)
    )


# --- shape and range contracts every caller relies on -------------------------


@pytest.mark.parametrize("method", METHODS)
def test_predictions_are_one_probability_per_row(method):
    rng = np.random.default_rng(5)
    raw = rng.random(150)
    calibrated = _fit(raw, (rng.random(150) < raw).astype(int), method).predict(raw)

    assert calibrated.shape == (150,)
    assert ((calibrated >= 0.0) & (calibrated <= 1.0)).all()


@pytest.mark.parametrize("method", METHODS)
def test_a_calibrator_survives_the_round_trip_to_disk(method, tmp_path):
    """The API serves the calibrator that training pickled, so equal predictions
    after save/load is part of the contract, not a joblib detail."""
    rng = np.random.default_rng(6)
    raw = rng.random(200)
    calibrator = _fit(raw, (rng.random(200) < raw).astype(int), method)

    path = str(tmp_path / "nested" / "calibrator.joblib")
    save_calibrator(calibrator, path)
    reloaded = load_calibrator(path)

    assert np.allclose(reloaded.predict(raw), calibrator.predict(raw))


def test_isotonic_clips_scores_outside_the_range_it_was_fitted_on():
    """Serving sees raw scores training never did; the calibrator must still return
    a probability rather than extrapolate off the end of its step function."""
    calibrator = _fit(np.linspace(0.2, 0.8, 100), (np.arange(100) >= 50).astype(int),
                      "isotonic")
    edges = calibrator.predict(np.array([-5.0, 0.0, 1.0, 5.0]))

    assert ((edges >= 0.0) & (edges <= 1.0)).all()
    assert edges[0] == edges[1]  # everything below the fitted range gets one value
    assert edges[2] == edges[3]  # ... and everything above it another


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError):
        build_calibrator("bayesian-vibes")


def test_no_calibrator_returns_raw():
    model = _FakeModel([0.1, 0.5, 0.9])
    assert np.allclose(churn_proba(model, X=None, calibrator=None), [0.1, 0.5, 0.9])
