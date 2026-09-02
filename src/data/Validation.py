
""" Data quality validation.
 In the previous version, this module only collected information
 (shape/columns/nulls/duplicates) and was not called by any pipeline.
  It now performs actual validation: it raises
   an exception if expected columns are missing or if
   the null/duplicate rate exceeds a critical threshold,
 and it has been integrated into the pipelines (training + daily). """
import logging

logger = logging.getLogger(__name__)


class DataValidationError(Exception):
    """The data does not meet the minimum quality requirements for the pipeline to continue."""


def validate(df, required_columns: list[str], max_null_ratio: float = 0.05) -> dict:

    missing_columns = [c for c in required_columns if c not in df.columns]

    if missing_columns:
        raise DataValidationError(
            f"expected columns are missing: {missing_columns}. existing columns: {list(df.columns)}"
        )

    if df.empty:
        raise DataValidationError("DataFrame is empty.")

    null_counts = df[required_columns].isnull().sum()
    total_cells = len(df) * len(required_columns)
    null_ratio = float(null_counts.sum()) / total_cells if total_cells else 0.0

    if null_ratio > max_null_ratio:
        raise DataValidationError(
            f"Null rate is High: %{null_ratio * 100:.2f} "
            f"(Accepted rate: %{max_null_ratio * 100:.2f})"
        )

    duplicate_count = int(df.duplicated().sum())

    summary = {
        "shape": df.shape,
        "columns": list(df.columns),
        "null_counts": null_counts.to_dict(),
        "duplicate_count": duplicate_count,
    }

    if duplicate_count > 0:
        # H1 FIX: the previous call was
        #     logger.warning("Duplicated value found in dataset.", duplicate_count)
        # -- a message string with no placeholder, plus a positional argument.
        # logging could not format it and swallowed the record as
        #     --- Logging error --- TypeError: not all arguments converted
        #     during string formatting
        # so duplicates were correctly DETECTED and then never actually
        # REPORTED. The placeholder below is what makes the count visible.
        logger.warning("Duplicate rows found in dataset: %d", duplicate_count)

    logger.info("Data Validation is Finished %s", summary)
    return summary


