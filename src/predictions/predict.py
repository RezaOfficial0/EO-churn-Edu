from src.model.calibrate import churn_proba


def predict(model, X, customer_info, *, threshold, calibrator=None):
    """Score every row, keep the ones at or above `threshold`, most-risky first.

    Returns `customer_info` (the id columns) with a `churn_probability` column added.
    """
    result = customer_info.copy()
    result["churn_probability"] = churn_proba(model, X, calibrator)
    at_risk = result[result["churn_probability"] >= threshold]
    return at_risk.sort_values("churn_probability", ascending=False)
