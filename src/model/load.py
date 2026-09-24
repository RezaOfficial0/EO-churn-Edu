import json
import os

from catboost import CatBoostClassifier

from config import CAT_COLS, FEATURES


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


def check_meta_matches_config(meta) -> None:
    """Refuse a model whose recorded feature list is not the one this process serves.

    SHAP contributions come back in column order, so a FEATURES list that was
    reordered (or renamed) since training attaches today's labels to yesterday's
    values: every explanation still looks plausible and points at the wrong feature.
    A missing list is not "probably fine" either - it means nothing can be checked.
    """
    if not meta:
        raise RuntimeError("model_meta.json is missing or empty; retrain before serving")
    for key, expected in (("features", FEATURES), ("cat_cols", CAT_COLS)):
        recorded = meta.get(key)
        if recorded is None:
            raise RuntimeError(f"model_meta.json has no {key!r}; retrain before serving")
        if list(recorded) != list(expected):
            raise RuntimeError(
                f"model_meta.json {key} does not match config: trained on {list(recorded)}, "
                f"serving {list(expected)}. Retrain, or restore the config the model was "
                f"trained with - order matters, not only membership."
            )
