"""End-to-end serving check on the committed model: feature engineering ->
predict -> at-risk table."""
import pandas as pd

from config import CALIBRATOR_PATH, MODEL_META_PATH, MODEL_PATH
from src.data.features import build_serving_frame
from src.data.preprocess import daily_process
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model
from src.predictions.predict import predict


def test_predict_returns_only_rows_at_or_above_threshold(daily_df):
    model = load_model(MODEL_PATH)
    calibrator = load_calibrator(CALIBRATOR_PATH)
    meta = load_meta(MODEL_META_PATH)
    threshold = meta["chosen_threshold"]

    engineered = build_serving_frame(daily_df, meta["imputation_values"])
    customer_info, X = daily_process(engineered)

    at_risk = predict(model, X, customer_info, threshold=threshold, calibrator=calibrator)

    assert (at_risk["churn_probability"] >= threshold).all()
    # sorted most-risky first
    assert at_risk["churn_probability"].is_monotonic_decreasing
    # every returned id is a real student from the input
    assert set(at_risk["student_id"]).issubset(set(daily_df["student_id"]))


def test_probabilities_are_in_the_unit_interval(daily_df):
    model = load_model(MODEL_PATH)
    calibrator = load_calibrator(CALIBRATOR_PATH)
    meta = load_meta(MODEL_META_PATH)

    engineered = build_serving_frame(daily_df, meta["imputation_values"])
    customer_info, X = daily_process(engineered)
    scored = predict(model, X, customer_info, threshold=0.0, calibrator=calibrator)

    assert scored["churn_probability"].between(0.0, 1.0).all()
    assert len(scored) == len(daily_df)
