"""Metrics for the churn model, computed once on the held-out test set.

At ~27% positives, ROC-AUC flatters the model; `average_precision` (PR-AUC) and
`precision_at_k` are the honest headline numbers. `brier_score` measures how well
the calibrated probabilities match reality.
"""
import logging

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

logger = logging.getLogger(__name__)


def effective_k(y_true, k) -> int:
    """The k that precision@k can actually be computed at: `min(k, rows)` (B-26).

    Asking for the top 20 of 12 students averages 12 rows. The number is not wrong,
    the LABEL is: `evaluate_model` used to call it `precision_at_20`, so a meta file
    from a small pilot reported a capacity metric for a capacity the test set never
    had. Truncating and saying so beats raising - a smaller test set must not kill a
    training run - but the name has to match what was measured.
    """
    rows = int(np.asarray(y_true).shape[0])
    return min(int(k or 0), rows)


def precision_at_k(y_true, churn_proba, k):
    """Of the k highest-risk students, what fraction actually churn?

    This is what a buyer asks: 'mentors can contact k students a week - how many
    of your top k are real?' With fewer than k rows the answer is precision at
    `effective_k` - see there, and use that name when reporting it.
    """
    y_true = np.asarray(y_true).astype(int)
    k_effective = effective_k(y_true, k)
    if not k_effective:
        return 0.0
    if k_effective < int(k):
        logger.warning(
            "precision@%d asked for on %d rows - reporting precision@%d instead",
            int(k), y_true.shape[0], k_effective,
        )
    top_k = np.argsort(churn_proba)[::-1][:k_effective]
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
    k_effective = effective_k(y_true, k)

    metrics = {
        "threshold": float(threshold),
        "roc_auc": float(roc_auc_score(y_true, proba)),
        "average_precision": float(average_precision_score(y_true, proba)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        # Named for the k actually measured, plus both numbers explicitly: a reader
        # of model_meta.json must not have to know how many test rows there were to
        # know what "precision_at_20" counted (B-26).
        "precision_at_k_requested": int(k or 0),
        "precision_at_k_effective": k_effective,
        f"precision_at_{k_effective}": precision_at_k(y_true, proba, k),
        f"lift_at_{k_effective}": lift_at_k(y_true, proba, k),
        "confusion_matrix": confusion_matrix(y_true, predicted).tolist(),
        "brier_score": float(brier_score_loss(y_true, proba)),
    }
    if raw_churn_proba is not None:
        raw = np.asarray(raw_churn_proba, dtype=float)
        metrics["brier_score_uncalibrated"] = float(brier_score_loss(y_true, raw))
        # Ranking metrics before calibration too. Isotonic regression is monotone,
        # so in theory it cannot change a ranking - in practice it maps whole
        # intervals onto one value, and every tie it creates costs ROC-AUC and
        # PR-AUC. When the calibrated numbers are the worse pair, calibration is
        # buying reliability with resolution, and that trade has to be visible
        # rather than inferred.
        metrics["roc_auc_uncalibrated"] = float(roc_auc_score(y_true, raw))
        metrics["average_precision_uncalibrated"] = float(
            average_precision_score(y_true, raw)
        )
        metrics["distinct_scores"] = int(np.unique(proba).size)
        metrics["distinct_scores_uncalibrated"] = int(np.unique(raw).size)
    return metrics
