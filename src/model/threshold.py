"""Pick the probability cut-off that minimises expected business cost.

A false alarm wastes one mentor outreach; a missed churn loses a student. Their
relative cost is `config.DECISION_COST`. We sweep every threshold from 0.01 to 0.99
on the validation set and keep the one with the lowest total cost.
"""
import numpy as np


def select_threshold(y_true, churn_proba, cost_false_alarm, cost_missed_churn):
    """Return the lowest-cost threshold and the mistakes it makes on this data."""
    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(churn_proba, dtype=float)

    best = None
    for threshold in np.round(np.arange(0.01, 1.0, 0.01), 2):
        predicted = (proba >= threshold).astype(int)
        false_alarms = int(((predicted == 1) & (y_true == 0)).sum())
        missed_churns = int(((predicted == 0) & (y_true == 1)).sum())
        total_cost = false_alarms * cost_false_alarm + missed_churns * cost_missed_churn

        if best is None or total_cost < best["expected_cost"]:
            best = {
                "threshold": float(threshold),
                "expected_cost": total_cost,
                "false_alarms": false_alarms,
                "missed_churns": missed_churns,
            }
    return best
