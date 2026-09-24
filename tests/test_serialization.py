import json

import numpy as np
import pandas as pd

from src.serialization import to_external, to_native


def test_numpy_scalars_become_python_scalars():
    result = to_native({"i": np.int64(3), "f": np.float64(1.5), "b": np.bool_(True)})
    assert result == {"i": 3, "f": 1.5, "b": True}
    assert isinstance(result["i"], int)
    assert isinstance(result["f"], float)


def test_missing_values_become_none():
    assert to_native(np.nan) is None
    assert to_native(pd.NaT) is None
    assert to_native(None) is None


def test_a_dataframe_row_round_trips_through_json():
    row = pd.DataFrame({"a": [1], "b": [2.5], "c": ["x"], "d": [np.nan]}).iloc[0]
    native = to_native(row.to_dict())
    assert json.loads(json.dumps(native)) == {"a": 1, "b": 2.5, "c": "x", "d": None}


def test_to_external_handles_a_whole_frame():
    """to_external is what every response body goes through, so it has to accept the
    frames and rows the endpoints actually hold - not only dicts."""
    frame = pd.DataFrame(
        {
            "student_id": ["STU1", "STU2"],
            "enrollment_date": ["2025-01-01", np.nan],
            "churn_probability": [np.float64(0.5), np.float64(0.25)],
        }
    )
    records = to_external(frame)
    assert json.loads(json.dumps(records)) == [
        {"student_id": "STU1", "enrollment_date": "2025-01-01", "churn_probability": 0.5},
        {"student_id": "STU2", "enrollment_date": None, "churn_probability": 0.25},
    ]


def test_to_external_handles_one_row():
    row = pd.DataFrame({"a": [np.int64(1)], "b": [np.nan]}).iloc[0]
    assert to_external(row) == {"a": 1, "b": None}
