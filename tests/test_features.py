"""The feature-engineering recipe must exactly reproduce data/updated_data.csv and
must never drop rows at serving time."""
import pandas as pd

from config import TRAIN_DATA_PATH
from src.data.features import (
    MISSING_FLAG_COLUMNS,
    build_serving_frame,
    build_training_frame,
)

# Median-by-plan_type values learned from the full training data (see test below
# that they match) - used to exercise the serving path without retraining.
LEARNED_IMPUTATION = {
    "weekly_study_hours_actual": {"Aylık": 9.0, "3 Aylık": 9.0, "Yıllık": 9.2, "_global": 9.0},
    "satisfaction_survey_score": {"Aylık": 3.6, "3 Aylık": 3.5, "Yıllık": 3.6, "_global": 3.6},
}


def test_build_training_frame_reproduces_updated_data(raw_df):
    expected = pd.read_csv(TRAIN_DATA_PATH)

    engineered, _learned = build_training_frame(raw_df)
    engineered = engineered[list(expected.columns)]

    a = engineered.sort_values("student_id").reset_index(drop=True)
    b = expected.sort_values("student_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_learned_imputation_matches_the_hardcoded_values(raw_df):
    _engineered, learned = build_training_frame(raw_df)
    assert learned == LEARNED_IMPUTATION


def test_missing_flags_are_1_exactly_where_the_value_was_null(raw_df):
    kept = raw_df.dropna(
        subset=["mentor_contact_freq_per_month", "message_response_time_hours"]
    )
    engineered, _ = build_training_frame(raw_df)
    engineered = engineered.sort_values("student_id").reset_index(drop=True)
    kept = kept.sort_values("student_id").reset_index(drop=True)

    for source_column, flag_column in MISSING_FLAG_COLUMNS.items():
        was_null = kept[source_column].isnull().astype(int).to_numpy()
        assert (engineered[flag_column].to_numpy() == was_null).all()


def test_serving_frame_keeps_every_row_and_fills_every_null(daily_df):
    served = build_serving_frame(daily_df, LEARNED_IMPUTATION)

    assert len(served) == len(daily_df)  # no rows dropped
    for source_column in MISSING_FLAG_COLUMNS:
        assert served[source_column].isnull().sum() == 0
