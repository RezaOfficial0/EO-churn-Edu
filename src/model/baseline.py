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


def single_rule_baseline(X_test, y_test, column="days_since_last_contact"):
    """Rank students by one column, no model at all."""
    score = np.asarray(X_test[column], dtype=float)
    return {
        "column": column,
        "roc_auc": float(roc_auc_score(y_test, score)),
        "average_precision": float(average_precision_score(y_test, score)),
    }
