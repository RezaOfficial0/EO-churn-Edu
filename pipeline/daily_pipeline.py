"""Score today's students, keep the risky ones, explain them, and log the run.

The run is split into two halves on purpose:

  - `score_students()` is **pure**: it reads the daily data and returns the at-risk
    students. It writes nothing. `GET /students` calls only this, so a dashboard
    can refresh as often as it likes without touching the alert log.
  - `log_alerts()` is the **write** half: it marks each student `new` /
    `still_at_risk` against the previous run and appends the run to the alert log.

`run_daily_pipeline()` is score + log, which is what `POST /run-daily-pipeline`
and the standalone entry point call:

    python -m pipeline.daily_pipeline

Whether "the daily data" and "the alert log" mean CSV files or Postgres tables is
decided by `config.DATA_SOURCE`; this module only talks to the dispatchers in
`src.data.loader`.
"""
import logging
from datetime import datetime, timezone

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
from src.data.features import RAW_FEATURE_COLUMNS, build_serving_frame
from src.data.loader import append_to_alert_log, load_daily_students, previous_at_risk_ids
from src.data.preprocess import daily_process
from src.data.validation import require_no_nulls, validate
from src.explainer.shap_explainer import create_explainer, explain_customers
from src.logging_setup import configure_logging
from src.model.calibrate import load_calibrator
from src.model.load import check_meta_matches_config, load_meta, load_model
from src.predictions.predict import predict
from src.serialization import to_native

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]


def score_students(
    daily_data_path=DAILY_DATA_PATH,
    *,
    model,
    explainer,
    imputation_values,
    threshold,
    calibrator=None,
    top_n=SHAP_TOP_N_FEATURES,
) -> pd.DataFrame:
    """Score today's students and return the ones at or above `threshold`.

    Read-only: no alert-log write, no `status` column. Columns returned are
    `student_id`, `enrollment_date`, `churn_probability`, `top_reasons`,
    `top_reasons_detail`, `features`, most-risky first.

    `daily_data_path` is used only when `DATA_SOURCE` is "csv"; in "db" mode the
    students come from the `daily_students` table and the path is ignored.
    """
    raw = load_daily_students(daily_data_path)
    # Raw data is allowed nulls in the columns we impute, so skip the null-ratio
    # check here; require_no_nulls below is the real gate, after imputation.
    validate(raw, STUDENT_INFO + RAW_FEATURE_COLUMNS, max_null_ratio=1.0)

    engineered = build_serving_frame(raw, imputation_values)
    require_no_nulls(engineered, FEATURES)

    customer_info, X = daily_process(engineered)
    at_risk = predict(model, X, customer_info, threshold=threshold, calibrator=calibrator)

    # Explain only the risky rows - SHAP over every row would be the bottleneck at scale.
    risky_X = X.loc[at_risk.index]
    explanations = explain_customers(explainer, risky_X, top_n=top_n)

    at_risk = at_risk.reset_index(drop=True)
    at_risk["top_reasons"] = [
        ", ".join(f"{reason['feature']} ({reason['impact']:+.2f})" for reason in reasons)
        for reasons in explanations
    ]
    at_risk["top_reasons_detail"] = explanations
    # The feature values the model actually scored (flags computed, nulls imputed),
    # so the dashboard can show them next to the SHAP reasons.
    at_risk["features"] = [to_native(row.to_dict()) for _, row in risky_X.iterrows()]
    return at_risk


def log_alerts(at_risk: pd.DataFrame, alerts_path=DAILY_ALERTS_PATH) -> pd.DataFrame:
    """Mark each student `new` / `still_at_risk` and append the run to the alert log.

    Returns the frame with the `status` column added. This is the only write in the
    daily flow - call it once per real run, never on a dashboard page load.
    """
    run_at = datetime.now(timezone.utc)
    marked = _mark_new_or_repeat(at_risk, alerts_path)
    append_to_alert_log(marked, alerts_path, run_at=run_at)
    return marked


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
) -> pd.DataFrame:
    """A full daily run: score, then record it. Unchanged behaviour and response shape."""
    at_risk = score_students(
        daily_data_path,
        model=model,
        explainer=explainer,
        imputation_values=imputation_values,
        threshold=threshold,
        calibrator=calibrator,
        top_n=top_n,
    )
    return log_alerts(at_risk, alerts_path)


def _mark_new_or_repeat(at_risk: pd.DataFrame, alerts_path) -> pd.DataFrame:
    """Add a `status` column: 'new' or 'still_at_risk' vs. the previous run."""
    previous = previous_at_risk_ids(alerts_path)
    at_risk = at_risk.copy()
    at_risk["status"] = [
        "still_at_risk" if str(student_id) in previous else "new"
        for student_id in at_risk[_ID_COLUMN]
    ]
    return at_risk


def main() -> None:
    configure_logging()
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH) or {}
    # Fail before writing anything. A run whose feature order no longer matches the
    # model, or whose calibrator is missing (load_calibrator raises), would still
    # produce a list and a morning message - with the wrong students on it.
    check_meta_matches_config(meta)
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
