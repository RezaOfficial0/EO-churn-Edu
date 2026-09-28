from catboost import CatBoostClassifier

from config import CAT_COLS, MODEL_PARAMS


# The settings that define *this project's* classifier rather than a hyperparameter
# search: balanced class weights (churn is ~27%), AUC for early stopping, a fixed
# seed so a rerun reproduces the model, and no per-iteration output in logs.
# They live in a dict instead of in the call so `params` can override any of them
# (B-26): passing `random_state=7` used to raise
# "got multiple values for keyword argument 'random_state'", which made the seed the
# one thing a caller could not vary - exactly what a seed-stability check needs.
MODEL_DEFAULTS = {
    "auto_class_weights": "Balanced",
    "eval_metric": "AUC",
    "random_state": 42,
    "verbose": False,
}


def build_model(cat_features=CAT_COLS, **params):
    """Create an untrained CatBoost classifier.

    `cat_features` is passed as column *names* (CatBoost accepts them directly),
    so it does not matter where the categorical columns sit in the frame.
    `params` overrides `config.MODEL_PARAMS` (iterations / depth / learning_rate)
    and MODEL_DEFAULTS above.
    """
    settings = {**MODEL_DEFAULTS, **MODEL_PARAMS, **params}
    return CatBoostClassifier(cat_features=cat_features, **settings)
