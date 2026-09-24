"""Turn pandas / numpy values into plain Python types that JSON can serialise.

`DataFrame.to_dict()` hands back numpy scalars (`int64`, `float64`) and `NaN`,
which FastAPI cannot encode. `to_native` walks a value (including nested dicts and
lists) and converts numpy scalars to `int` / `float` / `bool` and any missing
value to `None`.

`to_external` is the same conversion with the pandas containers handled too, and it
is the one function every API response body goes through - see its docstring for
why that matters.
"""
import pandas as pd


def to_external(value):
    """Make any value safe to put in a response body. Use this on every output path.

    `to_native` was applied to the `features` dict only, while the id columns went
    out straight from `to_dict(orient="records")`. A single empty `enrollment_date`
    in the daily data therefore left a float `NaN` in the body; Starlette encodes
    with `allow_nan=False`, so three endpoints answered
    `{"detail": "internal server error"}` for what is ordinary missing data. A
    student without an enrollment date is a `null` in the response, not a 500.

    Accepting frames and rows (not only dicts) is the point: a caller cannot forget
    the `.to_dict()` step and reintroduce the bug.
    """
    if isinstance(value, pd.DataFrame):
        return [to_external(record) for record in value.to_dict(orient="records")]
    if isinstance(value, pd.Series):
        return to_external(value.to_dict())
    # Recurse rather than hand the whole container to `to_native`: a frame or a row
    # usually sits one level down, under a key like "students".
    if isinstance(value, dict):
        return {key: to_external(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_external(item) for item in value]
    return to_native(value)


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
