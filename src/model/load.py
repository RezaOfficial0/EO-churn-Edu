import os
from catboost import CatBoostClassifier

def load_model(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Model file not found: {path}")
    model = CatBoostClassifier()
    try:
        model.load_model(path)
    except Exception as e:
        raise RuntimeError(f"Could not load model from {path}: {e}")
    return model
