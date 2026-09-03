"""The feature-engineering recipe: turn the raw export into model-ready columns.

The raw file (`data/mentorluk_churn_veriseti.csv`) has nulls in four columns.
This module is the ONE place the cleanup logic lives, so:
  - the training data can be regenerated (scripts/build_training_data.py), and
  - serving computes exactly the same columns (pipeline/daily_pipeline.py, the API).

Recipe (verified to reproduce `data/updated_data.csv` from the raw file exactly):

  1. drop_unimputable_rows - drop rows missing a value we cannot fill:
       mentor_contact_freq_per_month, message_response_time_hours
  2. add_missing_flags     - for weekly_study_hours_actual and satisfaction_survey_score,
                             add a 0/1 column recording whether the value was missing,
                             BEFORE it gets filled
  3. fit_imputation        - learn the median of each of those two columns within each
                             plan_type group (on training data)
  4. apply_imputation      - fill the missing values with the learned medians

At serving time steps 1 is skipped (never drop a customer's row - `validate()` rejects
a row that is missing an unimputable column instead) and step 3 is skipped (the medians
learned at training time are reused, and travel with the model in model_meta.json).
"""
import pandas as pd

# Rows missing any of these are dropped from the training data - there is no
# sensible value to impute for them.
UNIMPUTABLE_REQUIRED = [
    "mentor_contact_freq_per_month",
    "message_response_time_hours",
]

# {column that may be missing: name of its 0/1 "was missing" flag}
MISSING_FLAG_COLUMNS = {
    "weekly_study_hours_actual": "weekly_study_hours_actual_missing",
    "satisfaction_survey_score": "satisfaction_missing",
}

# Missing values are filled with the median within the row's group of this column.
IMPUTE_GROUP_COLUMN = "plan_type"


def drop_unimputable_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Training only: drop rows with a null in an unimputable column."""
    return df.dropna(subset=UNIMPUTABLE_REQUIRED).reset_index(drop=True)


def add_missing_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add the 0/1 "was missing" flag for each column in MISSING_FLAG_COLUMNS."""
    df = df.copy()
    for source_column, flag_column in MISSING_FLAG_COLUMNS.items():
        df[flag_column] = df[source_column].isnull().astype(int)
    return df


def fit_imputation(df: pd.DataFrame) -> dict:
    """Learn the fill values from training data.

    Returns a plain (JSON-serialisable) dict:
        {column: {group_value: median, ..., "_global": overall_median}}
    The "_global" median is the fallback for a group not seen during training.
    """
    learned: dict[str, dict[str, float]] = {}
    for column in MISSING_FLAG_COLUMNS:
        by_group = df.groupby(IMPUTE_GROUP_COLUMN)[column].median()
        learned[column] = {str(group): float(value) for group, value in by_group.items()}
        learned[column]["_global"] = float(df[column].median())
    return learned


def apply_imputation(df: pd.DataFrame, learned: dict) -> pd.DataFrame:
    """Fill the missing values in MISSING_FLAG_COLUMNS using `learned` (from fit_imputation)."""
    df = df.copy()
    for column, medians_by_group in learned.items():
        fallback = medians_by_group["_global"]
        fill_values = df[IMPUTE_GROUP_COLUMN].map(medians_by_group).fillna(fallback)
        df[column] = df[column].fillna(fill_values)
    return df


def build_training_frame(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Run the whole recipe on raw training data.

    Returns (engineered_frame, learned_imputation). `learned_imputation` must be
    saved with the model and passed to `add_missing_flags` + `apply_imputation`
    at serving time.
    """
    df = drop_unimputable_rows(raw_df)
    df = add_missing_flags(df)
    learned = fit_imputation(df)
    df = apply_imputation(df, learned)
    return df, learned


def build_serving_frame(raw_df: pd.DataFrame, learned: dict) -> pd.DataFrame:
    """Run the recipe on incoming data at serving time: add flags, fill with the
    medians learned at training time. Never drops rows."""
    df = add_missing_flags(raw_df)
    df = apply_imputation(df, learned)
    return df
