"""Metrics for the churn model, computed once on the held-out test set.

At ~27% positives, ROC-AUC flatters the model; `average_precision` (PR-AUC) and
`precision_at_k` are the honest headline numbers. `brier_score` measures how well
the calibrated probabilities match reality.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def precision_at_k(y_true, churn_proba, k):
    """Of the k highest-risk students, what fraction actually churn?

    This is what a buyer asks: 'mentors can contact k students a week - how many
    of your top k are real?'
    """
    y_true = np.asarray(y_true).astype(int)
    if not k:
        return 0.0
    top_k = np.argsort(churn_proba)[::-1][:k]
    return float(y_true[top_k].mean())


def lift_at_k(y_true, churn_proba, k):
    """precision@k divided by the base churn rate (1.0 == no better than random)."""
    base_rate = float(np.asarray(y_true).astype(int).mean())
    if not base_rate:
        return 0.0
    return precision_at_k(y_true, churn_proba, k) / base_rate


def false_negative_breakdown(X_test, y_test, churn_proba, threshold, group_columns):
    """For each categorical column, how many real churners the model missed, by group.

    This is the material for a customer conversation: 'which students does it miss?'
    """
    frame = X_test.reset_index(drop=True).copy()
    frame["_actual"] = np.asarray(y_test).astype(int)
    frame["_predicted"] = (np.asarray(churn_proba, dtype=float) >= threshold).astype(int)
    churners = frame[frame["_actual"] == 1]

    breakdown = {}
    for column in group_columns:
        caught = churners.groupby(column)["_predicted"]
        breakdown[column] = {
            str(group): {"churners": int(total), "missed": int(total - caught_count)}
            for group, total, caught_count in zip(
                caught.size().index, caught.size(), caught.sum()
            )
        }
    return breakdown


def evaluate_model(y_true, churn_proba, *, threshold, k, raw_churn_proba=None):
    """Return the full metrics dict for the test set at the chosen threshold."""
    y_true = np.asarray(y_true).astype(int)
    proba = np.asarray(churn_proba, dtype=float)
    predicted = (proba >= threshold).astype(int)

    metrics = {
        "threshold": float(threshold),
        "roc_auc": float(roc_auc_score(y_true, proba)),
        "average_precision": float(average_precision_score(y_true, proba)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        f"precision_at_{k}": precision_at_k(y_true, proba, k),
        f"lift_at_{k}": lift_at_k(y_true, proba, k),
        "confusion_matrix": confusion_matrix(y_true, predicted).tolist(),
        "brier_score": float(brier_score_loss(y_true, proba)),
    }
    if raw_churn_proba is not None:
        metrics["brier_score_uncalibrated"] = float(
            brier_score_loss(y_true, np.asarray(raw_churn_proba, dtype=float))
        )
    return metrics
