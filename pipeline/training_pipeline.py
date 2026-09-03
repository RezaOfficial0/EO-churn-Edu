"""Train the churn model end to end.

    load raw CSV
      -> feature engineering (src/data/features.py)
      -> validate
      -> train / val / test split
      -> fit CatBoost (early stopping on val)
      -> calibrate probabilities on val
      -> pick the alert threshold on val (minimise business cost)
      -> evaluate ONCE on test
      -> cross-validate + baselines + error analysis
      -> save model + model_meta.json + calibrator
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import (
    CALIBRATOR_PATH,
    CAT_COLS,
    DECISION_COST,
    FEATURES,
    MODEL_META_PATH,
    MODEL_PARAMS,
    MODEL_PATH,
    METRICS_DIR,
    PRECISION_AT_K,
    RAW_DATA_PATH,
    STUDENT_INFO,
    TARGET_FEATURE,
)
from src.data.features import build_training_frame
from src.data.loader import data_loader
from src.data.preprocess import split_features_target, split_train_val_test
from src.data.validation import validate
from src.logging_setup import configure_logging
from src.model.baseline import logistic_regression_baseline, single_rule_baseline
from src.model.calibrate import churn_proba, fit_calibrator, raw_churn_proba, save_calibrator
from src.model.cross_validate import cross_validated_auc
from src.model.evaluate import evaluate_model, false_negative_breakdown
from src.model.model import build_model
from src.model.save import save_model_and_meta
from src.model.threshold import select_threshold
from src.model.train import train

logger = logging.getLogger(__name__)


def run_training_pipeline(raw_data_path=RAW_DATA_PATH, model_path=MODEL_PATH):
    configure_logging()

    raw = data_loader(raw_data_path)
    engineered, imputation_values = build_training_frame(raw)
    engineered = validate(engineered, STUDENT_INFO + FEATURES + [TARGET_FEATURE])

    X, y = split_features_target(engineered)
    X_train, X_val, X_test, y_train, y_val, y_test = split_train_val_test(X, y)

    model = build_model()
    train(model, X_train, y_train, X_val, y_val)
    calibrator = fit_calibrator(model, X_val, y_val)

    threshold_selection = select_threshold(
        y_val,
        churn_proba(model, X_val, calibrator),
        cost_false_alarm=DECISION_COST["false_alarm"],
        cost_missed_churn=DECISION_COST["missed_churn"],
    )
    chosen_threshold = threshold_selection["threshold"]

    test_proba = churn_proba(model, X_test, calibrator)
    metrics = evaluate_model(
        y_test,
        test_proba,
        threshold=chosen_threshold,
        k=PRECISION_AT_K,
        raw_churn_proba=raw_churn_proba(model, X_test),
    )
    cv_auc = cross_validated_auc(X, y)
    baselines = {
        "logistic_regression": logistic_regression_baseline(
            X_train, y_train, X_test, y_test
        ),
        "single_rule": single_rule_baseline(X_test, y_test),
    }
    error_analysis = false_negative_breakdown(
        X_test, y_test, test_proba, chosen_threshold, CAT_COLS
    )

    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_synthetic_data": True,
        "data_file": str(raw_data_path),
        "data_rows": int(len(engineered)),
        "data_sha256": _sha256_of_file(raw_data_path),
        "features": FEATURES,
        "cat_cols": CAT_COLS,
        "model_params": MODEL_PARAMS,
        "catboost_tree_count": int(model.tree_count_),
        "decision_cost": DECISION_COST,
        "chosen_threshold": chosen_threshold,
        "threshold_selection": threshold_selection,
        "imputation_values": imputation_values,
        "calibrator_path": str(CALIBRATOR_PATH),
        "metrics": metrics,
        "cv_auc_mean": cv_auc["mean"],
        "cv_auc_std": cv_auc["std"],
        "baseline_metrics": baselines,
        "error_analysis_false_negatives": error_analysis,
    }

    save_calibrator(calibrator, CALIBRATOR_PATH)
    save_model_and_meta(model, model_path, MODEL_META_PATH, meta)
    _persist_metrics_history(meta)
    _log_summary(meta)

    return {"model": model, "calibrator": calibrator, "meta": meta}


def _sha256_of_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _persist_metrics_history(meta: dict) -> None:
    """Keep a timestamped copy of every training run under metrics/ (for history).
    The source of truth is still model_meta.json next to the model."""
    metrics_dir = Path(METRICS_DIR)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = metrics_dir / f"train_metrics_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    logger.info("training metrics saved: %s", out_path)


def _log_summary(meta: dict) -> None:
    m = meta["metrics"]
    logger.info("--- training summary (synthetic data) ---")
    logger.info("trees grown            : %s / %s", meta["catboost_tree_count"],
                meta["model_params"]["iterations"])
    logger.info("cv roc-auc             : %.3f +/- %.3f", meta["cv_auc_mean"], meta["cv_auc_std"])
    logger.info("test roc-auc           : %.3f", m["roc_auc"])
    logger.info("test pr-auc (avg prec) : %.3f", m["average_precision"])
    logger.info("baseline logreg pr-auc : %.3f", meta["baseline_metrics"]["logistic_regression"]["average_precision"])
    logger.info("chosen threshold       : %.2f", meta["chosen_threshold"])
    logger.info("test precision / recall: %.3f / %.3f", m["precision"], m["recall"])
    logger.info("brier calib / raw      : %.4f / %.4f", m["brier_score"],
                m.get("brier_score_uncalibrated", float("nan")))
