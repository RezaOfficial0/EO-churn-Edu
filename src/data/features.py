"""The feature-engineering recipe: turn the raw export into model-ready columns.

The raw file (`data/mentorluk_churn_veriseti.csv`) has nulls in four columns.
This module is the ONE place the cleanup logic lives, so:
  - the training data can be regenerated (scripts/build_training_data.py), and
  - serving computes exactly the same columns (pipeline/daily_pipeline.py, the API).

Recipe (verified to reproduce `data/updated_data.csv` from the raw file exactly):

  0. apply_contact_lag       - use the mentor-contact columns as they stood BEFORE the
                               prediction window, not as they stand at scoring time
                               (B-21). A no-op unless config.CONTACT_LAG_DAYS is set.
  0b. drop_audited_out_columns - remove the raw columns that are deliberately not
                               model features (config.AUDITED_OUT_FEATURES, B-21)
  1. drop_unimputable_rows - drop rows missing a value we cannot fill:
       mentor_contact_freq_per_month, message_response_time_hours
  1b. add_monthly_value    - derive monthly_value_try from the plan price and the
                             number of months that price covers
  2. add_missing_flags     - for weekly_study_hours_actual and satisfaction_survey_score,
                             add a 0/1 column recording whether the value was missing,
                             BEFORE it gets filled
  3. fit_imputation        - learn the median of each of those two columns within each
                             plan_type group (on training data)
  4. apply_imputation      - fill the missing values with the learned medians

At serving time step 1 is skipped here and step 3 is skipped (the medians learned at
training time are reused, and travel with the model in model_meta.json). The rows step
1 would have dropped are handled one level up instead: `pipeline/daily_pipeline.py`
quarantines them (B-28) using SERVING_REQUIRED_COLUMNS below, so one unusable row
costs that row and not the whole run. Step 0b also runs one level up on the serving
path, BEFORE the validation gate, because that gate rejects unexpected columns and
an audited-out column is exactly that: expected in the export, not a model input.

The temporal-validity reasoning behind steps 0 and 0b is in docs/LEAKAGE_AUDIT.md.
"""
import logging

import pandas as pd

from config import (
    AUDITED_OUT_FEATURES,
    CONTACT_FEATURES,
    CONTACT_LAG_DAYS,
    FEATURES,
    PLAN_MONTHS,
    TREND_COLUMNS,
    TREND_FEATURES,
    TREND_TOLERANCE_DAYS,
)

logger = logging.getLogger(__name__)

# {column that may be missing: name of its 0/1 "was missing" flag}
MISSING_FLAG_COLUMNS = {
    "weekly_study_hours_actual": "weekly_study_hours_actual_missing",
    "satisfaction_survey_score": "satisfaction_missing",
}

# Missing values are filled with the median within the row's group of this column.
IMPUTE_GROUP_COLUMN = "plan_type"

# --- Contact-column lag (B-21) ----------------------------------------------
# {contact column: the column an export must supply to state its PRE-WINDOW value}.
#
# A churn model is only an early-warning system if every input was knowable before
# the window the label is measured over. For the mentor-contact columns that is not
# automatic: "days since a mentor last made contact", read at scoring time, spans
# the window it is supposed to predict. The fix is to use the value as it stood when
# the window opened - and the ONLY honest source of that value is the export, per
# row. See docs/LEAKAGE_AUDIT.md for why it cannot be reconstructed from a snapshot.
LAGGED_CONTACT_SOURCES = {
    column: f"{column}_at_window_start" for column in CONTACT_FEATURES
}


def apply_contact_lag(df: pd.DataFrame, lag_days: int = CONTACT_LAG_DAYS) -> pd.DataFrame:
    """Replace each in-use contact column with its value at the window start.

    `lag_days` is documentation and a switch, not arithmetic: nothing here subtracts
    it from anything. A lag is either supplied by the export, row by row, or it does
    not exist, and this function will not invent one.

      lag_days <= 0
          off. Returns `df` unchanged. This is the setting for the synthetic dataset
          in data/, where the contact columns are dropped instead - see
          config.AUDITED_OUT_FEATURES.
      lag_days > 0
          for every column in config.CONTACT_FEATURES that is actually in
          config.FEATURES, the export must carry `<column>_at_window_start`. That
          column's value replaces the scoring-time one and the source column is
          removed, so the frame stays exactly the model's feature set. A missing
          source column raises: a lag that silently did not happen is worse than no
          lag, because the metrics then look like a fixed model.

    Columns parked by the audit are ignored on purpose: a lag for a column the model
    does not read is not a requirement to put on a client's export.
    """
    if lag_days <= 0:
        return df

    in_use = [column for column in CONTACT_FEATURES if column in FEATURES]
    if not in_use:
        logger.info(
            "CONTACT_LAG_DAYS=%d but no contact column is in FEATURES - nothing to lag",
            lag_days,
        )
        return df

    missing = [
        LAGGED_CONTACT_SOURCES[column]
        for column in in_use
        if LAGGED_CONTACT_SOURCES[column] not in df.columns
    ]
    if missing:
        raise ValueError(
            f"CONTACT_LAG_DAYS={lag_days} requires the pre-window value of every "
            f"contact feature, and these columns are not in the data: {missing}. "
            "Supply them in the export, or take the contact features out of "
            "config.FEATURES (see docs/LEAKAGE_AUDIT.md). This is not faked from the "
            "scoring-time value."
        )

    df = df.copy()
    for column in in_use:
        source = LAGGED_CONTACT_SOURCES[column]
        df[column] = df[source]
        df = df.drop(columns=[source])
    logger.info(
        "contact lag applied (%d day(s)): %s taken from %s",
        lag_days,
        in_use,
        [LAGGED_CONTACT_SOURCES[c] for c in in_use],
    )
    return df


def drop_audited_out_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the raw columns config.AUDITED_OUT_FEATURES parks (B-21).

    They are present in the export and not model inputs, so they have to go before
    anything that insists the frame is exactly the feature set - `validate()` on the
    serving path, and the `STUDENT_INFO + FEATURES + TARGET_FEATURE` shape on the
    training path.

    A name that has been re-admitted to config.FEATURES is never dropped, whatever
    AUDITED_OUT_FEATURES still says; config's own consistency check refuses that
    contradiction outright.
    """
    parked = [
        column
        for column in AUDITED_OUT_FEATURES
        if column in df.columns and column not in FEATURES
    ]
    if not parked:
        return df
    return df.drop(columns=parked)

# {model feature computed here: the raw columns it is computed from}. Like the
# missing-flags, these are NOT expected in a raw record - this module derives them.
DERIVED_COLUMNS = {"monthly_value_try": ["monthly_fee_try", "plan_type"]}

# The columns a RAW record (a CSV row, a daily_students row) must provide: every
# model feature except the ones this module computes, plus the raw inputs those
# computations need. Defined here because it follows from the recipe below -
# importers should not re-derive it.
RAW_FEATURE_COLUMNS = [
    column
    for column in FEATURES
    if column not in MISSING_FLAG_COLUMNS.values() 
    and column not in DERIVED_COLUMNS 
    and column not in TREND_COLUMNS
]
RAW_FEATURE_COLUMNS += [
    source
    for sources in DERIVED_COLUMNS.values()
    for source in sources
    if source not in RAW_FEATURE_COLUMNS
]

# The raw columns a row must actually HAVE A VALUE IN to be scorable (B-28).
#
# Everything in RAW_FEATURE_COLUMNS except the two the recipe can fill: a null in
# `weekly_study_hours_actual` or `satisfaction_survey_score` is recorded by its
# `*_missing` flag and filled with the median learned at training, so the row is
# still a row the model knows how to score. A null in any other column has nothing
# behind it - `drop_unimputable_rows` is the training-time counterpart, and this is
# the same decision made per row at serving time instead of per frame.
SERVING_REQUIRED_COLUMNS = [
    column for column in RAW_FEATURE_COLUMNS if column not in MISSING_FLAG_COLUMNS
]

# Training drops exactly the rows serving would quarantine. One definition, both
# sides, derived from the feature set - so a feature added or removed later keeps
# them in step by construction.
#
# These were two different lists until a customer export was run through training
# for the first time. UNIMPUTABLE_REQUIRED was a hand-written pair of columns: the
# ones the ONE file we trained on happened to have nulls in. The comment here used
# to say the difference was deliberate, on the grounds that "a null `plan_type` or
# `grade` reaches CatBoost as an unseen category rather than as an error". That is
# not what happens. CatBoost refuses a NaN in a categorical feature outright:
#
#     CatBoostError: bad object for id: nan
#     Invalid type for cat_feature[...]=nan : cat_features must be integer or
#     string, real number values and NaN values should be converted to string.
#
# Nothing in that names the column or the student, and a blank "parent involvement"
# cell is ordinary in a real export - so the first customer file was where it fired.
# The old list was also wrong in the other direction: it held
# `mentor_contact_freq_per_month`, which the B-21 audit parks and
# drop_audited_out_columns discards a few lines later. Training was throwing away
# rows over a column the model never sees.
UNIMPUTABLE_REQUIRED = SERVING_REQUIRED_COLUMNS


def add_monthly_value(df: pd.DataFrame) -> pd.DataFrame:
    """Derive `monthly_value_try` from the plan price and how many months it covers.

    `monthly_fee_try` is the plan's total price (see config.PLAN_MONTHS), so it
    separates the three plans perfectly and says nothing plan_type does not. The
    per-month value does not, which is the point of computing it.

    The source column is REPLACED, not kept alongside: this is a unit conversion,
    and leaving both in would hand the model the same fact twice - which is the
    problem being fixed. It also keeps the engineered frame exactly
    STUDENT_INFO + FEATURES + TARGET_FEATURE, which `validate()` insists on.

    Idempotent by design: POST /predict sends model features directly, so a frame
    that already carries `monthly_value_try` and no `monthly_fee_try` is returned
    untouched rather than overwritten with nulls.
    """
    if "monthly_fee_try" not in df.columns:
        return df

    df = df.copy()
    months = df["plan_type"].map(PLAN_MONTHS)

    unknown = sorted(set(df.loc[months.isna(), "plan_type"].dropna().astype(str)))
    if unknown:
        logger.warning(
            "plan_type not in config.PLAN_MONTHS: %s - treating the price as monthly",
            unknown,
        )
    months = months.fillna(1)

    df["monthly_value_try"] = (df["monthly_fee_try"] / months).round(2)
    return df.drop(columns=["monthly_fee_try"])


def drop_unimputable_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Training only: drop rows with a null in an unimputable column."""
    # Sadece GELEN kolonlara bak. Eksik bir zorunlu kolon burada pandas'tan
    # bir KeyError olarak degil, birkac satir sonra validate()'in cerceve
    # kapisindan adiyla birlikte raporlanmali - "missing required columns:
    # ['plan_type']" okunur, KeyError okunmaz.
    mevcut = [c for c in UNIMPUTABLE_REQUIRED if c in df.columns]
    return df.dropna(subset=mevcut).reset_index(drop=True)


def add_trend_features(current: pd.DataFrame, history: pd.DataFrame, as_of,
                       trend_cfg: dict = TREND_FEATURES) -> pd.DataFrame:
    """Add lag-delta features from historical snapshots.

    For each (col, n) in trend_cfg, computes:
      `<col>_delta_<n>d`         = current[col] − value of col ~n days before as_of
      `<col>_delta_<n>d_missing` = 1 if no usable history was found, else 0

    History lookup tolerates TREND_TOLERANCE_DAYS around the exact lag date
    (uses the latest snapshot in [target − tolerance, target]). Students with
    no matching history get NaN for the delta and missing=1.
    """
    out = current.copy()
    as_of = pd.Timestamp(as_of)
    ids = out["student_id"].astype(str)
    for col, windows in trend_cfg.items():
        for n in windows:
            prev = pd.Series(float("nan"), index=out.index)
            if not history.empty and col in history.columns:
                target = as_of - pd.Timedelta(days=n)
                window = history[
                    (history["as_of_date"] <= target)
                    & (history["as_of_date"] >= target - pd.Timedelta(days=TREND_TOLERANCE_DAYS))
                ]
                past = (window.sort_values("as_of_date")
                        .drop_duplicates("student_id", keep="last")
                        .set_index("student_id")[col])
                prev = ids.map(past)
            out[f"{col}_delta_{n}d"] = out[col] - prev
            out[f"{col}_delta_{n}d_missing"] = prev.isna().astype(int)
    return out


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
    df = apply_contact_lag(raw_df)
    df = drop_unimputable_rows(df)
    df = add_monthly_value(df)
    df = drop_audited_out_columns(df)
    df = add_missing_flags(df)
    learned = fit_imputation(df)
    df = apply_imputation(df, learned)
    return df, learned


def build_serving_frame(raw_df: pd.DataFrame, learned: dict) -> pd.DataFrame:
    """Run the recipe on incoming data at serving time: add flags, fill with the
    medians learned at training time. Never drops rows."""
    df = apply_contact_lag(raw_df)
    df = add_monthly_value(df)
    df = drop_audited_out_columns(df)
    df = add_missing_flags(df)
    df = apply_imputation(df, learned)
    return df
