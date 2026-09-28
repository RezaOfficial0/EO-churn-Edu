"""The 3-way split must be disjoint (no row used both to tune and to report) and
must keep the class balance."""
import numpy as np
import pandas as pd

from config import CAT_COLS, FEATURES, STUDENT_INFO, TARGET_FEATURE
from src.data.preprocess import (
    cast_categoricals,
    daily_process,
    split_features_target,
    split_train_val_test,
)


def _xy(n=1000):
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n)})
    y = pd.Series((rng.random(n) < 0.27).astype(int))
    return X, y


def test_split_sizes_add_up_and_do_not_overlap():
    X, y = _xy()
    X_train, X_val, X_test, y_train, y_val, y_test = split_train_val_test(X, y)

    assert len(X_train) + len(X_val) + len(X_test) == len(X)
    assert len(y_train) + len(y_val) + len(y_test) == len(y)

    indices = [set(X_train.index), set(X_val.index), set(X_test.index)]
    assert indices[0].isdisjoint(indices[1])
    assert indices[0].isdisjoint(indices[2])
    assert indices[1].isdisjoint(indices[2])


def test_split_is_roughly_60_20_20():
    X, y = _xy()
    X_train, X_val, X_test, *_ = split_train_val_test(X, y)
    assert abs(len(X_train) / len(X) - 0.60) < 0.02
    assert abs(len(X_val) / len(X) - 0.20) < 0.02
    assert abs(len(X_test) / len(X) - 0.20) < 0.02


def test_split_is_stratified():
    X, y = _xy()
    _, _, _, y_train, y_val, y_test = split_train_val_test(X, y)
    rates = [part.mean() for part in (y_train, y_val, y_test)]
    assert max(rates) - min(rates) < 0.03


# --- the split contract B-22 will be changing (B-26) --------------------------


def test_the_split_is_reproducible_for_the_same_seed():
    """Two runs of running_train_pipeline.py on the same data must produce the same
    model, and the split is the first place that can stop being true."""
    X, y = _xy()

    first = split_train_val_test(X, y)
    second = split_train_val_test(X, y)

    for a, b in zip(first, second):
        assert list(a.index) == list(b.index)


def test_a_different_seed_gives_a_different_split():
    """Otherwise the seed is decoration and `random_state` is silently ignored."""
    X, y = _xy()

    default_seed = split_train_val_test(X, y)[0]
    other_seed = split_train_val_test(X, y, random_state=7)[0]

    assert list(default_seed.index) != list(other_seed.index)


def test_every_row_lands_in_exactly_one_split():
    """Stronger than the size check: no row may be both tuned on and reported on, and
    none may be dropped on the floor either."""
    X, y = _xy()
    X_train, X_val, X_test, *_ = split_train_val_test(X, y)

    seen = list(X_train.index) + list(X_val.index) + list(X_test.index)
    assert sorted(seen) == sorted(X.index)
    assert len(seen) == len(set(seen))


def test_the_labels_stay_with_their_rows():
    """A shuffled split that pairs X_train with someone else's y_train trains a model
    on noise and reports a plausible ROC-AUC while doing it."""
    X, y = _xy()
    X_train, X_val, X_test, y_train, y_val, y_test = split_train_val_test(X, y)

    for X_part, y_part in ((X_train, y_train), (X_val, y_val), (X_test, y_test)):
        assert list(X_part.index) == list(y_part.index)
        assert y_part.tolist() == y.loc[X_part.index].tolist()


def _engineered_frame(n=8):
    """A frame shaped exactly like the output of build_training_frame."""
    frame = pd.DataFrame({column: np.arange(n, dtype=float) for column in FEATURES})
    for column in CAT_COLS:
        frame[column] = [f"c{i % 2}" for i in range(n)]
    for column in STUDENT_INFO:
        frame[column] = [f"{column}-{i}" for i in range(n)]
    frame[TARGET_FEATURE] = [i % 2 for i in range(n)]
    return frame


def test_x_is_exactly_the_configured_features_in_order():
    """Selecting FEATURES explicitly is what keeps a stray column in a customer CSV
    from becoming an accidental model input - and column ORDER is load-bearing,
    because SHAP contributions come back positionally (src/model/load.py)."""
    frame = _engineered_frame()
    frame["leaked_churn_date"] = "2026-01-01"

    X, y = split_features_target(frame)

    assert list(X.columns) == FEATURES
    assert "leaked_churn_date" not in X.columns
    assert y.tolist() == frame[TARGET_FEATURE].tolist()


def test_categoricals_reach_catboost_as_strings():
    """CatBoost is given cat_features by name and rejects a float column among them;
    a numeric `grade` in a customer export is the usual way this happens."""
    frame = _engineered_frame()
    frame["grade"] = [11, 12] * 4

    X, _ = split_features_target(frame)

    for column in CAT_COLS:
        assert X[column].map(type).eq(str).all()


def test_cast_categoricals_does_not_touch_the_caller_s_frame():
    frame = _engineered_frame()
    frame["grade"] = [11, 12] * 4

    cast_categoricals(frame)

    assert frame["grade"].tolist() == [11, 12] * 4


def test_daily_process_splits_ids_from_model_input_and_keeps_them_aligned():
    """The at-risk table is built by putting `customer_info` and the scores back
    side by side, so both halves must come back on the same 0..n-1 index."""
    frame = _engineered_frame().iloc[[5, 2, 7]]  # a filtered daily batch

    customer_info, X = daily_process(frame)

    assert list(customer_info.columns) == STUDENT_INFO
    assert list(X.columns) == FEATURES
    assert list(customer_info.index) == [0, 1, 2] == list(X.index)
    assert customer_info["student_id"].tolist() == ["student_id-5", "student_id-2",
                                                    "student_id-7"]
