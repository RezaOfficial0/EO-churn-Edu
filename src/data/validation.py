"""Data-quality gate.

`validate()` raises `DataValidationError` when the data would produce a wrong or
broken prediction, so the pipeline stops here instead of failing later (or worse,
succeeding quietly on bad input). It also drops exact-duplicate rows.

Two levels, and the difference is the whole point of B-28:

  - FRAME level (`validate`, `require_no_nulls`): the data as a whole is unusable -
    a column is missing, the frame is empty, ids repeat. The run must stop.
  - ROW level (`quarantine_unusable_rows`, `check_quarantine`): individual rows
    cannot be scored. The run continues on the rest and reports how many it dropped,
    because failing 24.999 scorable students over one half-written row means nobody
    gets a message - and `check_quarantine` is what stops that mercy from hiding an
    actual outage.
"""
import logging
from dataclasses import dataclass, field

import pandas as pd

from config import (
    MAX_NULL_RATIO_PER_COLUMN,
    MAX_QUARANTINE_RATIO,
    QUARANTINE_WARN_RATIO,
    STUDENT_INFO,
)

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


# --- Row-level quarantine (B-28) --------------------------------------------
# Added to each rejected row: the required columns that were null in it. Column
# names only - B-12 rules apply to everything that can reach a log or a response.
QUARANTINE_REASON_COLUMN = "quarantine_reason"


@dataclass(frozen=True)
class QuarantineReport:
    """How many rows one run skipped, and why. Counts only - no student ever.

    `reasons` is {column name: how many rows were null in it}, which is what an
    operator needs to find the broken upstream job: "480 rows missing
    days_since_last_contact" names the export that half-finished.
    """

    total: int
    scored: int
    skipped: int
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        """Share of input rows that were skipped. 0.0 for an empty input."""
        return self.skipped / self.total if self.total else 0.0

    @property
    def reportable(self) -> bool:
        """True when this run's losses are worth a human's attention.

        One decision in one place: the daily message, the ops alert and the tests
        all ask this rather than each comparing against the threshold themselves.
        A handful of broken rows in a 25.000-row export is normal operations and
        putting it in a mentor's morning message every day would train them to
        ignore the message.
        """
        return self.skipped > 0 and self.ratio >= QUARANTINE_WARN_RATIO

    def as_record(self) -> dict:
        """The JSON-safe form written to config.RUN_QUALITY_PATH."""
        return {
            "total": self.total,
            "scored": self.scored,
            "skipped": self.skipped,
            "ratio": round(self.ratio, 6),
            "reasons": dict(self.reasons),
        }

    @classmethod
    def from_record(cls, record: dict) -> "QuarantineReport":
        """Rebuild a report from `as_record()`, tolerating a hand-edited file.

        `ratio` is deliberately NOT read back: it is derived from the counts, and a
        file whose ratio disagrees with them must not be able to talk the message
        into a different answer than the run itself reached.
        """
        reasons = record.get("reasons")
        return cls(
            total=int(record.get("total", 0) or 0),
            scored=int(record.get("scored", 0) or 0),
            skipped=int(record.get("skipped", 0) or 0),
            reasons={str(k): int(v) for k, v in (reasons or {}).items()},
        )


def quarantine_unusable_rows(
    df: pd.DataFrame,
    required_columns: list[str],
    *,
    id_column: str = STUDENT_INFO[0],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split `df` into (usable, quarantined) on "is every required value present?".

    A row is quarantined when any column in `required_columns` is null in it, or
    when `id_column` is null - an alert nobody can act on is not an alert, and the
    id is what a mentor looks the student up by.

    `required_columns` is `features.SERVING_REQUIRED_COLUMNS` on the daily path:
    every raw model input except the two the imputer fills. The quarantined frame
    gets `QUARANTINE_REASON_COLUMN` listing the offending columns, so a caller can
    hand the rows back to the customer without the caller having to re-derive why.

    Missing columns are NOT this function's business - `validate()` has already
    failed the run for them. A column absent from `df` is skipped here rather than
    quarantining every row for it.
    """
    present = [column for column in required_columns if column in df.columns]
    if id_column in df.columns and id_column not in present:
        present.append(id_column)

    if not present or df.empty:
        return df, df.iloc[0:0].assign(**{QUARANTINE_REASON_COLUMN: pd.Series(dtype=object)})

    null_mask = df[present].isnull()
    unusable = null_mask.any(axis=1)

    usable = df.loc[~unusable]
    rejected = df.loc[unusable].copy()
    rejected[QUARANTINE_REASON_COLUMN] = [
        ", ".join(sorted(null_mask.columns[row])) for _, row in null_mask.loc[unusable].iterrows()
    ]
    return usable, rejected


def check_quarantine(
    total: int,
    rejected: pd.DataFrame,
    *,
    max_ratio: float = MAX_QUARANTINE_RATIO,
) -> QuarantineReport:
    """Count the quarantined rows, log them, and raise if too many were lost.

    Returns the report when the run may continue. Raises `DataValidationError`
    when it may not, which is either of:

      - every row was unusable. Whatever `max_ratio` says, a run that scored nobody
        is an outage, and "0 students at risk today" is the most reassuring possible
        way to describe one.
      - more than `max_ratio` of the input was quarantined. A small share is normal
        upstream noise; half the school is a broken export, and a message built from
        the surviving half looks exactly like a quiet day.

    `total` is the row count BEFORE the split, so the ratio is share-of-input.
    """
    skipped = int(len(rejected))
    reasons: dict[str, int] = {}
    if skipped and QUARANTINE_REASON_COLUMN in rejected.columns:
        for reason in rejected[QUARANTINE_REASON_COLUMN]:
            for column in str(reason).split(", "):
                if column:
                    reasons[column] = reasons.get(column, 0) + 1

    report = QuarantineReport(
        total=int(total), scored=int(total) - skipped, skipped=skipped, reasons=reasons
    )

    if skipped:
        # Column names and counts, never a row or an id (B-12). This is the line an
        # operator greps for when the customer asks why the list got shorter.
        logger.warning(
            "quarantined %d of %d row(s) (%.2f%%) with a missing required value: %s",
            report.skipped,
            report.total,
            report.ratio * 100,
            reasons or "-",
        )

    if total and report.scored == 0:
        _fail(
            "every row was unusable: 0 scorable rows",
            f"every one of {total} row(s) was quarantined - the export is broken, "
            f"not the students. Null columns: {reasons}",
        )

    if report.ratio > max_ratio:
        _fail(
            f"too many unusable rows: {report.skipped} of {report.total} "
            f"({report.ratio:.1%}) above the {max_ratio:.0%} limit",
            f"quarantine ratio {report.ratio:.1%} is above the {max_ratio:.0%} limit "
            f"({report.skipped} of {report.total} row(s)). Null columns: {reasons}. "
            f"Raise MAX_QUARANTINE_RATIO only if this really is acceptable data.",
        )

    return report
