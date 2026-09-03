"""Score today's students, keep the risky ones, explain them, and log the run.

`POST /run-daily-pipeline` calls `run_daily_pipeline`, passing the model, explainer
and calibrator it already holds in memory. It also runs standalone:

    python -m pipeline.daily_pipeline
"""
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from config import (
    CALIBRATOR_PATH,
    DAILY_ALERTS_PATH,
    DAILY_DATA_PATH,
    FEATURES,
    MODEL_META_PATH,
    MODEL_PATH,
    SHAP_TOP_N_FEATURES,
    STUDENT_INFO,
)
from src.data.features import MISSING_FLAG_COLUMNS, build_serving_frame
from src.data.loader import data_loader
from src.data.preprocess import daily_process
from src.data.validation import require_no_nulls, validate
from src.explainer.shap_explainer import create_explainer, explain_customers
from src.logging_setup import configure_logging
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model
from src.predictions.predict import predict

logger = logging.getLogger(__name__)

# Columns a raw serving record provides: every model feature except the
# missing-flags, which feature engineering computes.
RAW_FEATURE_COLUMNS = [c for c in FEATURES if c not in MISSING_FLAG_COLUMNS.values()]

_ID_COLUMN = STUDENT_INFO[0]


def run_daily_pipeline(
    daily_data_path=DAILY_DATA_PATH,
    *,
    model,
    explainer,
    imputation_values,
    threshold,
    calibrator=None,
    top_n=SHAP_TOP_N_FEATURES,
    alerts_path=DAILY_ALERTS_PATH,
):
    raw = data_loader(daily_data_path)
    # Raw data is allowed nulls in the columns we impute, so skip the null-ratio
    # check here; require_no_nulls below is the real gate, after imputation.
    validate(raw, STUDENT_INFO + RAW_FEATURE_COLUMNS, max_null_ratio=1.0)

    engineered = build_serving_frame(raw, imputation_values)
    require_no_nulls(engineered, FEATURES)

    customer_info, X = daily_process(engineered)
    at_risk = predict(model, X, customer_info, threshold=threshold, calibrator=calibrator)

    # Explain only the risky rows - SHAP over every row would be the bottleneck at scale.
    explanations = explain_customers(explainer, X.loc[at_risk.index], top_n=top_n)

    at_risk = at_risk.reset_index(drop=True)
    at_risk["top_reasons"] = [
        ", ".join(f"{reason['feature']} ({reason['impact']:+.2f})" for reason in reasons)
        for reasons in explanations
    ]
    at_risk["top_reasons_detail"] = explanations

    at_risk = _mark_new_or_repeat(at_risk, alerts_path)
    _append_to_alert_log(at_risk, alerts_path)
    return at_risk


def _previous_at_risk_ids(alerts_path) -> set[str]:
    """Student ids flagged in the most recent run recorded in the alert log."""
    path = Path(alerts_path)
    if not path.exists():
        return set()
    log = pd.read_csv(path)
    if log.empty:
        return set()
    last_run = log["run_date"].max()
    return set(log.loc[log["run_date"] == last_run, _ID_COLUMN].astype(str))


def _mark_new_or_repeat(at_risk: pd.DataFrame, alerts_path) -> pd.DataFrame:
    """Add a `status` column: 'new' or 'still_at_risk' vs. the previous run."""
    previous = _previous_at_risk_ids(alerts_path)
    at_risk = at_risk.copy()
    at_risk["status"] = [
        "still_at_risk" if str(student_id) in previous else "new"
        for student_id in at_risk[_ID_COLUMN]
    ]
    return at_risk


def _append_to_alert_log(at_risk: pd.DataFrame, alerts_path) -> None:
    """Append this run's at-risk students to data/daily_alerts.csv."""
    columns = [_ID_COLUMN, "churn_probability", "status", "top_reasons"]
    rows = at_risk[columns].copy()
    rows.insert(0, "run_date", date.today().isoformat())

    path = Path(alerts_path)
    rows.to_csv(path, mode="a", header=not path.exists(), index=False)
    logger.info("daily run: %d at-risk students appended to %s", len(rows), alerts_path)


def main() -> None:
    configure_logging()
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH) or {}
    result = run_daily_pipeline(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta.get("imputation_values", {}),
        threshold=meta.get("chosen_threshold", 0.5),
    )
    shown = result[[_ID_COLUMN, "churn_probability", "status", "top_reasons"]]
    print(shown.to_string(index=False) if len(shown) else "no students at or above the threshold")


if __name__ == "__main__":
    main()
