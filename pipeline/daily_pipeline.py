"""Score today's students, keep the risky ones, explain them, and log the run.

The run is split into two halves on purpose:

  - `score_students()` is **pure**: it reads the daily data and returns the at-risk
    students, the rows it could not score, and a count of them. It writes nothing.
    `GET /students` calls only this, so a dashboard can refresh as often as it likes
    without touching the alert log.
  - `log_alerts()` is the **write** half: it marks each student `new` /
    `still_at_risk` against the previous run and appends the run to the alert log.

`run_daily_pipeline()` is score + log, which is what `POST /run-daily-pipeline`
and the standalone entry point call:

    python -m pipeline.daily_pipeline

Whether "the daily data" and "the alert log" mean CSV files or Postgres tables is
decided by `config.DATA_SOURCE`; this module only talks to the dispatchers in
`src.data.loader`.

Unusable rows (B-28) are skipped, not fatal: one student whose row is missing a value
nothing can impute costs that student, not the other 24.999. The run still fails
loudly when the share of skipped rows crosses `MAX_QUARANTINE_RATIO`, or when nothing
survived - see `src.data.validation.check_quarantine`.
"""
import logging
from datetime import datetime, timezone
from typing import NamedTuple

import pandas as pd

from config import (
    CALIBRATOR_PATH,
    DAILY_ALERTS_PATH,
    DAILY_DATA_PATH,
    FEATURES,
    MODEL_META_PATH,
    MODEL_PATH,
    RUN_QUALITY_PATH,
    SHAP_TOP_N_FEATURES,
    STUDENT_INFO,
)
from src.data.features import (
    RAW_FEATURE_COLUMNS,
    SERVING_REQUIRED_COLUMNS,
    build_serving_frame,
)
from src.data.loader import append_to_alert_log, load_daily_students, previous_at_risk_ids
from src.data.preprocess import daily_process
from src.data.run_quality import write_report
from src.data.validation import (
    QuarantineReport,
    check_quarantine,
    quarantine_unusable_rows,
    require_no_nulls,
    validate,
)
from src.explainer.shap_explainer import create_explainer, explain_customers
from src.logging_setup import configure_logging
from src.model.calibrate import load_calibrator
from src.model.load import check_meta_matches_config, load_meta, load_model
from src.notifications.ops import send_ops_alert
from src.predictions.predict import predict
from src.serialization import to_native

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]


class ScoringResult(NamedTuple):
    """What one scoring pass produced.

    A tuple so callers that want everything can unpack it in one line, named so the
    ones that only want the list (`result.at_risk`) do not index into it. `quarantine`
    holds counts only and is safe to log or persist; `rejected` holds real student
    rows and is not (B-12) - it exists so a caller can hand them back to the customer.
    """

    at_risk: pd.DataFrame
    rejected: pd.DataFrame
    quarantine: QuarantineReport


def score_students(
    daily_data_path=DAILY_DATA_PATH,
    *,
    model,
    explainer,
    imputation_values,
    threshold,
    calibrator=None,
    top_n=SHAP_TOP_N_FEATURES,
) -> ScoringResult:
    """Score today's students and return the ones at or above `threshold`.

    Read-only: no alert-log write, no `status` column. Returns a `ScoringResult`
    (`at_risk`, `rejected`, `quarantine`). `at_risk` columns are `student_id`,
    `enrollment_date`, `churn_probability`, `top_reasons`, `top_reasons_detail`,
    `features`, most-risky first.

    Rows that cannot be scored are quarantined rather than failing the run (B-28);
    `check_quarantine` still raises `DataValidationError` when too many were lost or
    when none survived, so a broken export can never look like a quiet day.

    `daily_data_path` is used only when `DATA_SOURCE` is "csv"; in "db" mode the
    students come from the `daily_students` table and the path is ignored.
    """
    raw = load_daily_students(daily_data_path)
    # Raw data is allowed nulls in the columns we impute, so skip the null-ratio
    # check here. This is the FRAME gate (missing columns, empty input, repeated
    # ids) - everything it rejects is unusable as a whole, so it still stops the run.
    raw = validate(raw, STUDENT_INFO + RAW_FEATURE_COLUMNS, max_null_ratio=1.0)

    # The ROW gate. SERVING_REQUIRED_COLUMNS is every raw model input except the two
    # the imputer fills, so what is left here is a row with a hole nothing can close.
    usable, rejected = quarantine_unusable_rows(raw, SERVING_REQUIRED_COLUMNS)
    quarantine = check_quarantine(len(raw), rejected)

    engineered = build_serving_frame(usable, imputation_values)
    # Unchanged, and now a bug detector rather than a data gate: every row-level null
    # the recipe does not fill was removed above, so if this still fires the recipe
    # and SERVING_REQUIRED_COLUMNS have drifted apart.
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
    return ScoringResult(at_risk=at_risk, rejected=rejected, quarantine=quarantine)


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
    quality_path=RUN_QUALITY_PATH,
) -> ScoringResult:
    """A full daily run: score, record it, and leave the quality summary behind.

    Returns the same `ScoringResult` as `score_students`, with `status` added to
    `at_risk`. The quality summary is written HERE and not in `score_students`,
    because that one is called on every dashboard page load and must stay pure.
    """
    result = score_students(
        daily_data_path,
        model=model,
        explainer=explainer,
        imputation_values=imputation_values,
        threshold=threshold,
        calibrator=calibrator,
        top_n=top_n,
    )
    marked = log_alerts(result.at_risk, alerts_path)
    # Counts only, for scripts/send_daily_alerts.py to put in the message. Written
    # even when nothing was skipped: a stale file from a worse run yesterday must not
    # be reported as today's.
    write_report(quality_path, result.quarantine)
    return result._replace(at_risk=marked)


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
    quarantine = result.quarantine
    if quarantine.skipped:
        print(
            f"skipped {quarantine.skipped} of {quarantine.total} row(s) "
            f"({quarantine.ratio:.1%}) that could not be scored: {quarantine.reasons}"
        )
    if quarantine.reportable:
        # The operator channel, not the customer's group: "480 rows are missing
        # days_since_last_contact" is a message for whoever can fix the export.
        # Below the warn ratio this stays in the log - see QuarantineReport.reportable.
        send_ops_alert(
            f"günlük koşu {quarantine.skipped}/{quarantine.total} satırı atladı "
            f"({quarantine.ratio:.1%}). Eksik kolonlar: {quarantine.reasons}"
        )
    shown = result.at_risk[[_ID_COLUMN, "churn_probability", "status", "top_reasons"]]
    print(shown.to_string(index=False) if len(shown) else "no students at or above the threshold")


if __name__ == "__main__":
    main()
