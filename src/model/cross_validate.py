"""Cross-validated ROC-AUC, so the single-split number gets an honest error bar.

Each fold repeats the real training procedure (fit with early stopping on an
internal validation slice), then scores the held-out fold.
"""
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split

from src.model.model import build_model
from src.model.calibrate import raw_churn_proba


def cross_validated_auc(X, y, n_splits=5, random_state=42):
    """Return {"mean": ..., "std": ..., "folds": [...]} of ROC-AUC across folds."""
    folds = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    scores = []
    for train_index, test_index in folds.split(X, y):
        X_fold_train, X_fold_test = X.iloc[train_index], X.iloc[test_index]
        y_fold_train, y_fold_test = y.iloc[train_index], y.iloc[test_index]

        X_fit, X_early_stop, y_fit, y_early_stop = train_test_split(
            X_fold_train,
            y_fold_train,
            test_size=0.2,
            random_state=random_state,
            stratify=y_fold_train,
        )
        model = build_model()
        model.fit(
            X_fit, y_fit, eval_set=(X_early_stop, y_early_stop), early_stopping_rounds=50
        )
        scores.append(
            float(roc_auc_score(y_fold_test, raw_churn_proba(model, X_fold_test)))
        )

    return {
        "mean": float(np.mean(scores)),
        "std": float(np.std(scores)),
        "folds": scores,
    }
