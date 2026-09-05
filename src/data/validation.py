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
        raise DataValidationError(
            f"missing required columns: {missing}. present: {list(df.columns)}"
        )

    if not allow_extra_columns:
        expected = set(required_columns) | set(STUDENT_INFO)
        unexpected = [c for c in df.columns if c not in expected]
        if unexpected:
            raise DataValidationError(
                f"unexpected columns: {unexpected}. An extra column would be picked up "
                f"as a model feature; drop it or pass allow_extra_columns=True."
            )

    if df.empty:
        raise DataValidationError("dataframe is empty")

    null_ratio = df[required_columns].isnull().mean()
    too_null = null_ratio[null_ratio > max_null_ratio]
    if not too_null.empty:
        offenders = {col: f"{ratio:.1%}" for col, ratio in too_null.items()}
        raise DataValidationError(
            f"columns above the {max_null_ratio:.0%} null limit: {offenders}"
        )

    if id_column in df.columns:
        duplicate_ids = df.loc[df[id_column].duplicated(), id_column].unique().tolist()
        if duplicate_ids:
            shown = duplicate_ids[:10]
            more = " ..." if len(duplicate_ids) > 10 else ""
            raise DataValidationError(f"duplicate {id_column} values: {shown}{more}")

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
        raise DataValidationError(f"null values remain after feature engineering: {offenders}")
