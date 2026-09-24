"""Data-quality gate.

`validate()` raises `DataValidationError` when the data would produce a wrong or
broken prediction, so the pipeline stops here instead of failing later (or worse,
succeeding quietly on bad input). It also drops exact-duplicate rows.
"""
import logging

import pandas as pd

from config import MAX_NULL_RATIO_PER_COLUMN, STUDENT_INFO

logger = logging.getLogger(__name__)


class DataValidationError(Exception):
    """The data does not meet the minimum quality bar for the pipeline to continue."""


def _fail(client_message: str, log_detail: str):
    """Log what went wrong, raise only what a caller may be told.

    `api/main.py` turns this exception's text straight into a 400 body, and these
    messages used to carry ten real student ids and the full internal column list.
    The operator needs the detail; the caller needs to know the data was rejected.
    """
    logger.error("data validation failed: %s", log_detail)
    raise DataValidationError(client_message)

## analyizez DataFrame and return ...
def validate(
    df: pd.DataFrame,
    required_columns: list[str],
    *,
    id_column: str = STUDENT_INFO[0],
    max_null_ratio: float = MAX_NULL_RATIO_PER_COLUMN,
    allow_extra_columns: bool = False,
) -> pd.DataFrame:
    """Return `df` with exact-duplicate rows removed, or raise `DataValidationError`.

    Checks, in order:
      1. every column in `required_columns` is present,
      2. no column outside `required_columns` + `STUDENT_INFO` is present
         (an extra column would silently become model feature #25),
      3. the frame is not empty,
      4. no single required column is more than `max_null_ratio` null
         (checked per column, not pooled across all of them),
      5. `id_column` has no duplicate values.
    """
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        _fail(
            f"missing required columns: {len(missing)}",
            f"missing required columns: {missing}. present: {list(df.columns)}",
        )

    if not allow_extra_columns:
        expected = set(required_columns) | set(STUDENT_INFO)
        unexpected = [c for c in df.columns if c not in expected]
        if unexpected:
            _fail(
                f"unexpected columns: {len(unexpected)} column(s) that are not part of "
                f"the model input",
                f"unexpected columns: {unexpected}. An extra column would be picked up "
                f"as a model feature; drop it or pass allow_extra_columns=True.",
            )

    if df.empty:
        _fail("dataframe is empty", "dataframe is empty")

    null_ratio = df[required_columns].isnull().mean()
    too_null = null_ratio[null_ratio > max_null_ratio]
    if not too_null.empty:
        offenders = {col: f"{ratio:.1%}" for col, ratio in too_null.items()}
        _fail(
            f"columns above the {max_null_ratio:.0%} null limit: {len(offenders)}",
            f"columns above the {max_null_ratio:.0%} null limit: {offenders}",
        )

    if id_column in df.columns:
        duplicate_ids = df.loc[df[id_column].duplicated(), id_column].unique().tolist()
        if duplicate_ids:
            # Not even the log gets the ids: they identify (mostly under-age) people,
            # and the duplicates are findable in the source data the operator already
            # has. The count is what tells them it happened.
            _fail(
                f"duplicate {id_column} values: {len(duplicate_ids)}",
                f"duplicate {id_column} values: {len(duplicate_ids)} distinct id(s) "
                f"(ids not logged - look them up in the source data)",
            )

    exact_duplicates = int(df.duplicated().sum())
    if exact_duplicates:
        logger.warning("dropping %d exact-duplicate row(s)", exact_duplicates)
        df = df.drop_duplicates()

    logger.info("validation ok: %d rows, %d columns", len(df), df.shape[1])
    return df


def require_no_nulls(df: pd.DataFrame, columns: list[str]) -> None:
    """Raise if any of `columns` still has a null. Used at serving time, after
    feature engineering, where a single missing value must not slip into the model."""
    null_counts = df[columns].isnull().sum()
    offenders = null_counts[null_counts > 0].to_dict()
    if offenders:
        _fail(
            f"null values remain after feature engineering: {len(offenders)} column(s)",
            f"null values remain after feature engineering: {offenders}",
        )
