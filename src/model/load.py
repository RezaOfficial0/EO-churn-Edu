import json
import os

from catboost import CatBoostClassifier


def load_model(path):
    """Load the CatBoost model file, or raise FileNotFoundError / RuntimeError."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"model file not found: {path}")
    model = CatBoostClassifier()
    try:
        model.load_model(path)
    except Exception as e:
        raise RuntimeError(f"could not load model from {path}: {e}")
    return model


def load_meta(path):
    """Load the model_meta.json sidecar, or return None if it does not exist."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
