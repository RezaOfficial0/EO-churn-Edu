import json

import numpy as np
import pandas as pd

from src.serialization import to_native


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
