"""The daily run must record every run and mark repeat students `still_at_risk`,
even when two runs happen on the same day."""
from config import CALIBRATOR_PATH, MODEL_META_PATH, MODEL_PATH
from pipeline.daily_pipeline import run_daily_pipeline
from src.explainer.shap_explainer import create_explainer
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model


def _run(alerts_path):
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH)
    return run_daily_pipeline(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta["imputation_values"],
        threshold=meta["chosen_threshold"],
        alerts_path=str(alerts_path),
    )


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
