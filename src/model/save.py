import json
import os


def save_model(model, path):
    """Write the CatBoost model file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        model.save_model(path)
    except Exception as e:
        raise RuntimeError(f"could not save model to {path}: {e}")


def save_model_and_meta(model, model_path, meta_path, meta):
    """Write the model file plus its `model_meta.json` sidecar.

    The sidecar ties the served model to its metrics: what data it was trained on,
    which features, the chosen threshold, the imputation values, and the numbers.
    `GET /metrics` reads this file, so it always describes the loaded model.
    """
    save_model(model, model_path)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
