from catboost import CatBoostClassifier

from config import CAT_COLS, MODEL_PARAMS


def build_model(cat_features=CAT_COLS, **params):
    """Create an untrained CatBoost classifier.

    `cat_features` is passed as column *names* (CatBoost accepts them directly),
    so it does not matter where the categorical columns sit in the frame.
    `params` overrides `config.MODEL_PARAMS` (iterations / depth / learning_rate).
    """
    settings = {**MODEL_PARAMS, **params}
    return CatBoostClassifier(
        **settings,
        cat_features=cat_features,
        auto_class_weights="Balanced",
        eval_metric="AUC",
        random_state=42,
        verbose=False,
    )
