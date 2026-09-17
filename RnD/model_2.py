import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from config import (
    FEATURES,
    STUDENT_INFO,
    TARGET_FEATURE,
    TRAIN_DATA_PATH,
    CAT_COLS,
)

from src.data.loader import data_loader
from src.data.Validation import validate
from src.data.preprocess import preprocess
from src.model.evaluate import evaluate_model

logger = logging.getLogger(__name__)


def _build_model(cat_features, class_weight=None):
    """Build the Logistic Regression model pipeline."""
    categorical_features = CAT_COLS.copy()
    numeric_features = [
        column
        for column in FEATURES
        if column not in categorical_features
    ]

    numeric_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )

    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )

    preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, numeric_features),
            ("cat", categorical_pipeline, categorical_features),
        ]
    )

    model = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                LogisticRegression(
                    max_iter=1000,
                    class_weight=class_weight,
                    random_state=42,
                ),
            ),
        ]
    )

    return model


def _evaluate_with_pr_auc(model, X_test, y_test):
    """Use the project's shared evaluator and add PR-AUC."""
    metrics = evaluate_model(model, X_test, y_test)

    probabilities = model.predict_proba(X_test)[:, 1]
    metrics["pr_auc"] = average_precision_score(
        y_test,
        probabilities,
    )

    return metrics


def _persist_metrics(metrics: dict, metrics_dir: str) -> None:
    """Persist model metrics as a JSON file."""
    Path(metrics_dir).mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(metrics_dir) / f"model_2_metrics_{stamp}.json"

    with open(out_path, "w", encoding="utf-8") as file:
        json.dump(
            metrics,
            file,
            indent=2,
            ensure_ascii=False,
        )

    logger.info("Model 2 metrics saved: %s", out_path)


def run_training_pipeline(
    train_data_path,
    model_save_path,
    metrics_dir: str = "metrics",
):
    """
    Train and evaluate Logistic Regression for EO-Churn Edu.

    Two variants are evaluated:
    1. Logistic Regression without class weight.
    2. Logistic Regression with class_weight='balanced'.

    The function follows the same callable structure and return
    format as the project's training pipeline.
    """

    # 1. Load data
    data = data_loader(train_data_path)

    # 2. Validate data
    validate(
        data,
        STUDENT_INFO + FEATURES + TARGET_FEATURE,
    )

    # 3. Use the project's existing preprocessing and split
    X_train, X_test, y_train, y_test, cat_features = preprocess(
        data,
        STUDENT_INFO,
        TARGET_FEATURE,
        CAT_COLS,
    )

    # 4. Train Logistic Regression without class weights
    model_no_weight = _build_model(
        cat_features,
        class_weight=None,
    )

    model_no_weight.fit(
        X_train,
        y_train,
    )

    metrics_no_weight = _evaluate_with_pr_auc(
        model_no_weight,
        X_test,
        y_test,
    )

    # 5. Train Logistic Regression with balanced class weights
    model_weighted = _build_model(
        cat_features,
        class_weight="balanced",
    )

    model_weighted.fit(
        X_train,
        y_train,
    )

    metrics_weighted = _evaluate_with_pr_auc(
        model_weighted,
        X_test,
        y_test,
    )

    # 6. Save the weighted model
    save_path = Path(model_save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_weighted, save_path)

    # 7. Keep both experiment results
    metrics = {
        "no_class_weight": metrics_no_weight,
        "class_weight": metrics_weighted,
    }

    # 8. Persist metrics
    _persist_metrics(
        metrics,
        metrics_dir,
    )

    # 9. Return pipeline-compatible result
    return {
        "model": model_weighted,
        "history": {
            "model": "Logistic Regression",
            "variants": [
                "no_class_weight",
                "class_weight",
            ],
        },
        "metrics": metrics,
    }


if __name__ == "__main__":
    result = run_training_pipeline(
        TRAIN_DATA_PATH,
        "saved_models/logistic_regression_churn.joblib",
    )

    print("\n=== Logistic Regression - No Class Weight ===")
    print(result["metrics"]["no_class_weight"])

    print("\n=== Logistic Regression - Class Weight ===")
    print(result["metrics"]["class_weight"])
