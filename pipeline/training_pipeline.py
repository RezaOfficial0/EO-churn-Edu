from config import FEATURES , STUDENT_INFO , TARGET_FEATURE , TRAIN_DATA_PATH , CAT_COLS , MODEL_PATH
from src.data.loader import data_loader
from src.data.Validation import validate
from src.data.preprocess import preprocess
from src.model.model import build_model
from src.model.train import train
from src.model.evaluate import evaluate_model
from src.model.save import save_model


import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)



def run_training_pipeline(train_data_path, model_save_path,metrics_dir: str = "metrics"):
    data = data_loader(train_data_path)
    validate(data, STUDENT_INFO + FEATURES + TARGET_FEATURE)  # egitim oncesi kolon/null kontrolu
    X_train, X_test, y_train, y_test, cat_features = preprocess(data, STUDENT_INFO, TARGET_FEATURE, CAT_COLS)
    model = build_model(300, 4, 0.05, cat_features)
    history = train(model, X_train, X_test, y_train, y_test)
    metrics = evaluate_model(model, X_test, y_test)


    save_model(model, model_save_path)


    _persist_metrics(metrics, metrics_dir)

    return {
        "model": model,
        "history": history,
        "metrics": metrics,
    }


def _persist_metrics(metrics: dict, metrics_dir: str) -> None:

    Path(metrics_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(metrics_dir) / f"train_metrics_{stamp}.json"

    serializable = {k: v for k, v in metrics.items() if k != "classification_report"}
    serializable["classification_report"] = metrics["classification_report"]

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise RuntimeError(f"Could not write metrics to {out_path}: {e}")

    logger.info("Training metrics has been Saved: %s", out_path)
