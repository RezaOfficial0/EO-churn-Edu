"""Turn pandas / numpy values into plain Python types that JSON can serialise.

`DataFrame.to_dict()` hands back numpy scalars (`int64`, `float64`) and `NaN`,
which FastAPI cannot encode. `to_native` walks a value (including nested dicts and
lists) and converts numpy scalars to `int` / `float` / `bool` and any missing
value to `None`.
"""
import pandas as pd


def to_native(value):
    if isinstance(value, dict):
        return {key: to_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_native(item) for item in value]
    if value is None:
        return None
    if pd.isna(value):  # NaN, NaT, pd.NA
        return None
    if hasattr(value, "item"):  # numpy scalar -> python scalar
        return value.item()
    return value
