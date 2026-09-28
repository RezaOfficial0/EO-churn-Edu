"""`build_model` is the only place the classifier's settings are decided (B-26).

One test here is the bug the issue names: `build_model(random_state=7)` raised
TypeError, because the seed was hard-coded in the constructor call AND reachable
through `**params`. The settings the project depends on - balanced class weights for
a ~27% positive rate, AUC for early stopping, a fixed seed, silent training - are
checked as well, since a change to any of them changes every number in
model_meta.json without changing a line of the pipeline.
"""
import pytest

from config import CAT_COLS, MODEL_PARAMS
from src.model.model import MODEL_DEFAULTS, build_model


def test_an_override_wins_instead_of_raising():
    """The B-26 defect: `**params` collided with the hard-coded keyword argument."""
    model = build_model(random_state=7)

    assert model.get_params()["random_state"] == 7


def test_the_seed_is_fixed_when_nobody_overrides_it():
    assert build_model().get_params()["random_state"] == MODEL_DEFAULTS["random_state"]


@pytest.mark.parametrize("name,value", sorted(MODEL_DEFAULTS.items()))
def test_every_default_is_overridable(name, value):
    """Not only random_state: the same collision would have hit any of them, and a
    caller that cannot set `verbose` or `eval_metric` cannot experiment at all."""
    replacement = {"auto_class_weights": "SqrtBalanced", "eval_metric": "Logloss",
                   "random_state": 7, "verbose": True}[name]
    assert build_model().get_params()[name] == value
    assert build_model(**{name: replacement}).get_params()[name] == replacement


def test_the_project_settings_are_the_ones_the_metrics_were_measured_with():
    params = build_model().get_params()

    # Balanced weights are why calibration exists at all (src/model/calibrate.py).
    assert params["auto_class_weights"] == "Balanced"
    assert params["eval_metric"] == "AUC"  # early stopping in src/model/train.py
    assert params["verbose"] is False
    assert params["cat_features"] == CAT_COLS


def test_hyperparameters_come_from_config_and_can_be_overridden():
    assert build_model().get_params()["iterations"] == MODEL_PARAMS["iterations"]
    assert build_model(iterations=5).get_params()["iterations"] == 5
    assert MODEL_PARAMS["iterations"] != 5  # config itself was not mutated


def test_cat_features_can_be_given_positionally_or_by_name():
    """`cross_validate` and the baselines build models for frames of their own."""
    assert build_model(["grade"]).get_params()["cat_features"] == ["grade"]
    assert build_model(cat_features=[]).get_params()["cat_features"] == []


def test_the_model_comes_back_untrained():
    """`build_model` allocates, `train` fits - a model that arrived fitted would make
    cross_validated_auc's folds leak into each other."""
    assert build_model().is_fitted() is False
