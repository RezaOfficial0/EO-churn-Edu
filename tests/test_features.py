"""The feature-engineering recipe must exactly reproduce data/updated_data.csv and
must never drop rows at serving time."""
import json
import logging
import math

import pandas as pd

from config import FEATURES, STUDENT_INFO, TARGET_FEATURE, TRAIN_DATA_PATH
from src.data.features import (
    DERIVED_COLUMNS,
    MISSING_FLAG_COLUMNS,
    RAW_FEATURE_COLUMNS,
    SERVING_REQUIRED_COLUMNS,
    add_missing_flags,
    add_monthly_value,
    apply_imputation,
    build_serving_frame,
    build_training_frame,
    drop_unimputable_rows,
    fit_imputation,
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


# --- the recipe, step by step (B-26) ------------------------------------------
#
# The parity test above covers the whole recipe on the real file, which is the
# strongest check there is - and also the one that says least about WHY a change
# broke it. These pin each step on a frame small enough to read, and pin the column
# contracts that B-21 (leakage audit) and B-22 (clean splits) will be editing.


def _tiny_raw():
    """Five rows, two plans, a null in each imputable column, one unimputable null.

    S4 is the row training drops (no mentor_contact_freq_per_month); every other row
    survives, so each group still has a value to take a median of.
    """
    return pd.DataFrame(
        {
            "student_id": ["S1", "S2", "S3", "S4", "S5"],
            "plan_type": ["Aylık", "Aylık", "Yıllık", "Yıllık", "Yıllık"],
            "monthly_fee_try": [1800.0, 1800.0, 16730.0, 16730.0, 16730.0],
            "weekly_study_hours_actual": [10.0, None, 4.0, 8.0, 6.0],
            "satisfaction_survey_score": [4.0, 3.0, None, 2.0, 5.0],
            "mentor_contact_freq_per_month": [5.0, 5.0, 5.0, None, 5.0],
            "message_response_time_hours": [2.0, 2.0, 2.0, 2.0, 2.0],
        }
    )


def test_monthly_value_is_the_plan_price_divided_by_the_months_it_covers():
    """The fix B-21 will be auditing: `monthly_fee_try` is a plan TOTAL, so left as
    it is it is a perfect proxy for plan_type and nothing more."""
    engineered = add_monthly_value(_tiny_raw())

    assert engineered["monthly_value_try"].tolist() == [
        1800.0, 1800.0, 1394.17, 1394.17, 1394.17,
    ]
    # Replaced, not kept alongside: two columns would hand the model the same fact
    # twice, and the engineered frame must be exactly STUDENT_INFO + FEATURES + churn.
    assert "monthly_fee_try" not in engineered.columns


def test_monthly_value_is_idempotent_for_an_already_engineered_frame():
    """POST /predict sends model features directly - `monthly_value_try` present and
    `monthly_fee_try` absent. Overwriting it with nulls was a real failure mode."""
    already = pd.DataFrame({"plan_type": ["Aylık"], "monthly_value_try": [1800.0]})

    pd.testing.assert_frame_equal(add_monthly_value(already), already)


def test_an_unknown_plan_treats_the_price_as_monthly_and_says_so(caplog):
    """A plan name this client invented must not kill the run - config.PLAN_MONTHS is
    edited per customer, and a warning the operator can act on is the right trade."""
    raw = pd.DataFrame({"plan_type": ["6 Aylık", None], "monthly_fee_try": [9000.0, 500.0]})

    with caplog.at_level(logging.WARNING):
        engineered = add_monthly_value(raw)

    assert engineered["monthly_value_try"].tolist() == [9000.0, 500.0]
    assert "6 Aylık" in caplog.text
    # A null plan_type is not reported as an unknown NAME, it just falls back to 1.
    assert "None" not in caplog.text and "nan" not in caplog.text


def test_only_unimputable_nulls_drop_a_training_row():
    """S4 has no mentor_contact_freq_per_month - nothing sensible to fill. S2 and S3
    are missing values the recipe CAN fill, so they stay."""
    kept = drop_unimputable_rows(_tiny_raw())

    assert kept["student_id"].tolist() == ["S1", "S2", "S3", "S5"]
    assert kept.index.tolist() == [0, 1, 2, 3]  # reset, so later .iloc/.loc agree


def test_missing_flags_are_added_before_the_values_are_filled():
    """Recipe order, and the whole point of the flags: after imputation the null is
    gone, so a flag computed afterwards would be 0 everywhere and the model would
    lose the single most predictive fact about that student."""
    engineered, _ = build_training_frame(_tiny_raw())

    assert engineered["weekly_study_hours_actual_missing"].tolist() == [0, 1, 0, 0]
    assert engineered["satisfaction_missing"].tolist() == [0, 0, 1, 0]
    assert engineered["weekly_study_hours_actual"].isnull().sum() == 0
    for flag_column in MISSING_FLAG_COLUMNS.values():
        assert engineered[flag_column].dtype.kind == "i"  # 0/1 int, not bool or float


def test_imputation_learns_a_median_per_plan_plus_a_global_fallback():
    learned = fit_imputation(add_missing_flags(drop_unimputable_rows(_tiny_raw())))

    # Aylık study hours: [10.0, null] -> 10.0. Yıllık: [4.0, 6.0] -> 5.0.
    # _global is the median over every kept row, [10.0, 4.0, 6.0] -> 6.0.
    assert learned["weekly_study_hours_actual"] == {
        "Aylık": 10.0,
        "Yıllık": 5.0,
        "_global": 6.0,
    }
    assert learned["satisfaction_survey_score"] == {
        "Aylık": 3.5,
        "Yıllık": 5.0,
        "_global": 4.0,
    }
    # Group keys are strings and values floats: this dict is written into
    # model_meta.json and read back at serving time.
    assert json.loads(json.dumps(learned)) == learned


def test_a_group_with_no_values_at_all_learns_a_nan_median():
    """Current behaviour, pinned because it is a rough edge rather than a decision.

    A plan whose every row is missing the column has no median, so `fit_imputation`
    stores NaN for that group. Serving still recovers - `apply_imputation` maps the
    group to NaN and then fills it with `_global` - but the NaN is written into
    model_meta.json as the bare token `NaN`, which `json.dump` emits happily and a
    strict JSON reader (any non-Python consumer of that file) rejects. Left as it is
    deliberately: B-26 is the test net, and changing the recipe's output is a
    separate decision. See the report.
    """
    raw = pd.DataFrame(
        {
            "plan_type": ["Aylık", "Yıllık"],
            "weekly_study_hours_actual": [8.0, None],
            "satisfaction_survey_score": [4.0, 4.0],
            "mentor_contact_freq_per_month": [5.0, 5.0],
            "message_response_time_hours": [2.0, 2.0],
        }
    )

    engineered, learned = build_training_frame(raw)

    assert math.isnan(learned["weekly_study_hours_actual"]["Yıllık"])
    assert "NaN" in json.dumps(learned)  # not valid JSON for a strict parser
    # The row is still filled, from the global median, so nothing is served as null.
    assert engineered["weekly_study_hours_actual"].tolist() == [8.0, 8.0]


def test_imputation_fills_only_the_nulls_and_leaves_every_other_value_alone():
    learned = {"weekly_study_hours_actual": {"Aylık": 9.0, "_global": 1.0}}
    raw = pd.DataFrame(
        {"plan_type": ["Aylık", "Aylık"], "weekly_study_hours_actual": [2.5, None]}
    )

    filled = apply_imputation(raw, learned)

    assert filled["weekly_study_hours_actual"].tolist() == [2.5, 9.0]
    assert raw["weekly_study_hours_actual"].isnull().sum() == 1  # input untouched


def test_a_plan_never_seen_in_training_falls_back_to_the_global_median():
    """A customer adds a plan after the model was trained. The row must still be
    scorable, with the overall median rather than a null CatBoost cannot take."""
    learned = {"weekly_study_hours_actual": {"Aylık": 9.0, "_global": 6.0}}
    raw = pd.DataFrame({"plan_type": ["Haftalık"], "weekly_study_hours_actual": [None]})

    assert apply_imputation(raw, learned)["weekly_study_hours_actual"].tolist() == [6.0]


def test_training_and_serving_produce_identical_features_for_the_same_rows(raw_df):
    """Train/serve parity, end to end: the same students through the training recipe
    and the serving recipe have to come out byte-identical in every model feature.
    Any difference here is a silent accuracy loss at serving time that no metric in
    model_meta.json can see."""
    sample = raw_df.head(300)

    trained, learned = build_training_frame(sample)
    # Serving never drops rows, so it is given the rows training kept.
    served = build_serving_frame(drop_unimputable_rows(sample), learned)

    pd.testing.assert_frame_equal(served[FEATURES], trained[FEATURES])


def test_the_engineered_frame_is_exactly_the_columns_the_model_is_served_on(raw_df):
    engineered, _ = build_training_frame(raw_df.head(50))

    assert set(engineered.columns) == set(STUDENT_INFO + FEATURES + [TARGET_FEATURE])


def test_the_raw_column_contract_follows_from_the_recipe():
    """`RAW_FEATURE_COLUMNS` is what the API and daily pipeline demand of an incoming
    record, so it must stay derived from FEATURES rather than hand-maintained: B-21
    and B-22 will be adding and removing features."""
    for flag_column in MISSING_FLAG_COLUMNS.values():
        assert flag_column not in RAW_FEATURE_COLUMNS  # the recipe computes these
    for derived, sources in DERIVED_COLUMNS.items():
        assert derived not in RAW_FEATURE_COLUMNS
        for source in sources:
            assert source in RAW_FEATURE_COLUMNS  # ... from these

    computed = set(MISSING_FLAG_COLUMNS.values()) | set(DERIVED_COLUMNS)
    assert set(FEATURES) - computed <= set(RAW_FEATURE_COLUMNS)


def test_a_row_may_be_scored_with_a_null_only_where_the_recipe_can_fill_it():
    """SERVING_REQUIRED_COLUMNS is the quarantine rule (B-28): exactly the raw
    columns the recipe has no value for."""
    assert set(SERVING_REQUIRED_COLUMNS) == set(RAW_FEATURE_COLUMNS) - set(
        MISSING_FLAG_COLUMNS
    )
    for column in MISSING_FLAG_COLUMNS:
        assert column not in SERVING_REQUIRED_COLUMNS


def test_the_raw_export_actually_provides_every_required_raw_column(raw_df, daily_df):
    """The contract is only worth anything if the two files we ship satisfy it."""
    for frame in (raw_df, daily_df):
        assert not set(SERVING_REQUIRED_COLUMNS) - set(frame.columns)
