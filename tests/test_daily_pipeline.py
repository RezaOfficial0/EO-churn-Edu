"""The daily run must record every run and mark repeat students `still_at_risk`,
even when two runs happen on the same day - and scoring alone must record nothing."""
import pandas as pd

from config import CALIBRATOR_PATH, MODEL_META_PATH, MODEL_PATH
from pipeline.daily_pipeline import log_alerts, run_daily_pipeline, score_students
from src.explainer.shap_explainer import create_explainer
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model


def _kwargs():
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH)
    return dict(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta["imputation_values"],
        threshold=meta["chosen_threshold"],
    )


def _run(alerts_path):
    return run_daily_pipeline(alerts_path=str(alerts_path), **_kwargs())


def test_second_run_marks_repeats_still_at_risk(tmp_path):
    alerts_path = tmp_path / "daily_alerts.csv"

    first = _run(alerts_path)
    assert (first["status"] == "new").all()

    second = _run(alerts_path)
    repeats = set(first["student_id"]) & set(second["student_id"])
    assert repeats  # the sample is deterministic, so there must be repeats
    still = second.loc[second["student_id"].isin(repeats), "status"]
    assert (still == "still_at_risk").all()

    log = alerts_path.read_text().strip().splitlines()
    assert len(log) == 1 + len(first) + len(second)  # header + both runs recorded


def test_score_students_writes_nothing(tmp_path):
    """`GET /students` calls this on every dashboard load - it must have no side effects."""
    alerts_path = tmp_path / "daily_alerts.csv"

    scored = score_students(**_kwargs())

    assert not alerts_path.exists()
    assert "status" not in scored.columns
    assert {"student_id", "churn_probability", "top_reasons", "top_reasons_detail", "features"} <= set(
        scored.columns
    )
    # Repeated scoring is stable, precisely because nothing was recorded in between.
    again = score_students(**_kwargs())
    pd.testing.assert_series_equal(scored["churn_probability"], again["churn_probability"])


def test_log_alerts_is_the_only_writer(tmp_path):
    alerts_path = tmp_path / "daily_alerts.csv"

    scored = score_students(**_kwargs())
    marked = log_alerts(scored, str(alerts_path))

    assert (marked["status"] == "new").all()
    assert alerts_path.exists()
    assert len(alerts_path.read_text().strip().splitlines()) == 1 + len(marked)


def test_score_students_matches_run_daily_pipeline(tmp_path):
    """Same students, same scores - the only difference is that one of them writes."""
    scored = score_students(**_kwargs())
    recorded = _run(tmp_path / "daily_alerts.csv")

    assert list(scored["student_id"]) == list(recorded["student_id"])
    pd.testing.assert_series_equal(scored["churn_probability"], recorded["churn_probability"])
