"""Cheap baselines the CatBoost model has to beat, so its complexity is justified."""
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from config import CAT_COLS, FEATURES

NUMERIC_FEATURES = [c for c in FEATURES if c not in CAT_COLS]


def logistic_regression_baseline(X_train, y_train, X_test, y_test):
    """One-hot encode categoricals, standardise numerics, fit logistic regression."""
    preprocess = ColumnTransformer(
        [
            ("categorical", OneHotEncoder(handle_unknown="ignore"), CAT_COLS),
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ]
    )
    model = make_pipeline(
        preprocess, LogisticRegression(max_iter=1000, class_weight="balanced")
    )
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    return {
        "roc_auc": float(roc_auc_score(y_test, proba)),
        "average_precision": float(average_precision_score(y_test, proba)),
    }


def single_rule_baseline(X_test, y_test, column="message_response_time_hours"):
    """Rank students by one column, no model at all.

    The default was `days_since_last_contact` until B-21, which was the strongest
    single column in the data (ROC-AUC 0.687, PR-AUC 0.486 on its own) and is no
    longer a feature: it described the mentor's behaviour, not the student's. The
    replacement is the strongest STUDENT-side column that survived the audit, and it
    is signed the right way round - higher response time, higher risk - so the raw
    number here is readable without inverting it.

    The column is a default, not a requirement. FEATURES is edited per client and
    this one may not survive that edit - a diagnostic baseline must never be the
    reason a training run dies, so a missing column is reported and skipped.
    """
    if column not in X_test.columns:
        return {
            "column": None,
            "roc_auc": None,
            "average_precision": None,
            "skipped": f"{column!r} is not in FEATURES",
        }

    score = np.asarray(X_test[column], dtype=float)
    return {
        "column": column,
        "roc_auc": float(roc_auc_score(y_test, score)),
        "average_precision": float(average_precision_score(y_test, score)),
    }
