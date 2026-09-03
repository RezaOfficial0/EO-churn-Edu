"""The 3-way split must be disjoint (no row used both to tune and to report) and
must keep the class balance."""
import numpy as np
import pandas as pd

from src.data.preprocess import split_train_val_test


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
