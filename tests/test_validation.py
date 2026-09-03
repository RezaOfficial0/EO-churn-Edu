import numpy as np
import pandas as pd
import pytest

from src.data.validation import DataValidationError, require_no_nulls, validate


def _good_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "student_id": ["a", "b", "c"],
            "score": [1.0, 2.0, 3.0],
            "flag": [0, 1, 0],
        }
    )


def test_accepts_a_clean_frame():
    out = validate(_good_frame(), ["score", "flag"], allow_extra_columns=True)
    assert len(out) == 3


def test_missing_required_column_raises():
    with pytest.raises(DataValidationError, match="missing required columns"):
        validate(_good_frame(), ["score", "does_not_exist"], allow_extra_columns=True)


def test_unexpected_column_raises():
    frame = _good_frame()
    frame["sneaky_extra"] = 1
    with pytest.raises(DataValidationError, match="unexpected columns"):
        validate(frame, ["score", "flag"])


def test_null_ratio_is_per_column_not_pooled():
    frame = _good_frame()
    # 'score' is 100% null; 'flag' is clean. Pooled null ratio would be ~33% and,
    # depending on the limit, might pass - per-column must catch 'score'.
    frame["score"] = np.nan
    with pytest.raises(DataValidationError, match="null limit"):
        validate(frame, ["score", "flag"], allow_extra_columns=True, max_null_ratio=0.5)


def test_duplicate_id_raises():
    frame = _good_frame()
    frame.loc[2, "student_id"] = "a"
    with pytest.raises(DataValidationError, match="duplicate student_id"):
        validate(frame, ["score", "flag"], allow_extra_columns=True)


def test_exact_duplicate_rows_are_dropped():
    frame = pd.concat([_good_frame(), _good_frame().iloc[[0]]], ignore_index=True)
    # first row now appears twice; ids collide, so validate against a non-id frame
    frame = frame.drop(columns="student_id")
    out = validate(frame, ["score", "flag"], allow_extra_columns=True)
    assert len(out) == 3


def test_require_no_nulls():
    frame = _good_frame()
    frame.loc[1, "score"] = np.nan
    with pytest.raises(DataValidationError, match="null values remain"):
        require_no_nulls(frame, ["score", "flag"])
