"""The feature-engineering recipe must exactly reproduce data/updated_data.csv and
must never drop rows at serving time."""
import json
import logging
import math

import pandas as pd
import pytest

import src.data.features as features_module
from config import (
    AUDITED_OUT_FEATURES,
    CAT_COLS,
    CONTACT_FEATURES,
    CONTACT_LAG_DAYS,
    FEATURES,
    STUDENT_INFO,
    TARGET_FEATURE,
    TRAIN_DATA_PATH,
    TREND_COLUMNS,
    TREND_TOLERANCE_DAYS,
)
from src.data.features import (
    DERIVED_COLUMNS,
    MISSING_FLAG_COLUMNS,
    RAW_FEATURE_COLUMNS,
    SERVING_REQUIRED_COLUMNS,
    UNIMPUTABLE_REQUIRED,
    add_missing_flags,
    add_monthly_value,
    apply_contact_lag,
    apply_imputation,
    build_serving_frame,
    build_training_frame,
    drop_audited_out_columns,
    drop_unimputable_rows,
    fit_imputation,
    add_trend_features,
)
from src.data.validation import quarantine_unusable_rows

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
    kept = raw_df.dropna(subset=UNIMPUTABLE_REQUIRED)
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
    """Bes satir, iki plan, doldurulabilir her kolonda birer bosluk.

    S4'un `mentor_contact_freq_per_month` degeri yok. Eskiden egitim bu yuzden
    S4'u atiyordu; artik atmiyor, cunku o kolonu B-21 denetimi feature setinden
    cikardi ve model onu hic gormuyor. Bir satiri, modelin kullanmadigi bir
    kolon bos diye atmak veriyi bosuna harcamakti.

    S5'in `parent_involvement` degeri yok: ATILAN satir artik bu. Kategorik bir
    bosluk impute edilemez ve CatBoost kategorik bir alanda NaN'i kabul etmez -
    skorlamada da ayni satir karantinaya aliniyor. Iki taraf ayni kurali
    uyguluyor, test de bunu tutuyor.
    """
    return pd.DataFrame(
        {
            "student_id": ["S1", "S2", "S3", "S4", "S5"],
            "grade": ["11. Sınıf", "12. Sınıf", "Mezun", "11. Sınıf", "12. Sınıf"],
            "track": ["Sayısal", "Sözel", "Sayısal", "Dil", "Sayısal"],
            "city_tier": ["Tier 1", "Tier 2", "Tier 1", "Tier 3", "Tier 2"],
            "parent_involvement": ["Yüksek", "Orta", "Orta", "Düşük", None],
            "plan_type": ["Aylık", "Aylık", "Yıllık", "Yıllık", "Yıllık"],
            "monthly_fee_try": [1800.0, 1800.0, 16730.0, 16730.0, 16730.0],
            "tenure_months": [3.0, 9.0, 14.0, 2.0, 7.0],
            "program_adherence_rate": [0.8, 0.6, 0.9, 0.5, 0.7],
            "weekly_study_hours_planned": [12.0, 10.0, 14.0, 9.0, 11.0],
            "weekly_study_hours_actual": [10.0, None, 4.0, 8.0, 6.0],
            "satisfaction_survey_score": [4.0, 3.0, None, 2.0, 5.0],
            "mentor_contact_freq_per_month": [5.0, 5.0, 5.0, None, 5.0],
            "message_response_time_hours": [2.0, 2.0, 2.0, 2.0, 2.0],
            "late_response_count_30d": [1, 0, 2, 3, 1],
            "trial_exam_count_total": [8, 12, 20, 4, 10],
            "trial_exam_avg_net": [50.0, 44.0, 61.0, 38.0, 55.0],
            "trial_exam_score_trend": [1.2, -0.4, 2.0, -1.8, 0.3],
            "missed_trial_exam_count": [0, 1, 0, 3, 1],
            "payment_delay_days_avg": [0.0, 2.0, 0.0, 9.0, 1.0],
            "support_ticket_count_90d": [0, 1, 0, 2, 1],
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
    """Atilan satir, SKORLAMANIN da karantinaya alacagi satirdir.

    S5'in `parent_involvement`'i yok: kategorik bir boslugun arkasinda hicbir sey
    yok, impute edilemez, ve CatBoost kategorik bir alanda NaN'i reddediyor.
    S2 ve S3'un bosluklari tarifin DOLDURABILDIGI kolonlarda, kaliyorlar.
    S4'un `mentor_contact_freq_per_month`'u yok ama o kolon B-21 ile feature
    setinden cikti - modelin gormedigi bir kolon yuzunden satir atilmaz.
    """
    kept = drop_unimputable_rows(_tiny_raw())

    assert kept["student_id"].tolist() == ["S1", "S2", "S3", "S4"]
    assert kept.index.tolist() == [0, 1, 2, 3]  # reset, so later .iloc/.loc agree


def test_training_and_serving_agree_on_what_an_unusable_row_is():
    """Iki taraf TEK bir tanimi paylasmali.

    Ayri durduklarinda ikisi de yanlisti: egitim, modelin gormedigi bir kolon
    bos diye satir atiyor; skorlamanin atacagi satiri ise atmiyor ve o satir
    CatBoost'a ulasip `CatBoostError: bad object for id: nan` uretiyordu. O
    mesajda ne kolon adi var ne ogrenci. Ilk gercek musteri disa aktariminda
    patlayan sey tam olarak buydu.
    """
    assert UNIMPUTABLE_REQUIRED == SERVING_REQUIRED_COLUMNS

    ham = _tiny_raw()
    egitimde_kalan = set(drop_unimputable_rows(ham)["student_id"])
    kullanilabilir, _ = quarantine_unusable_rows(ham, SERVING_REQUIRED_COLUMNS)
    skorlamada_kalan = set(kullanilabilir["student_id"])

    assert egitimde_kalan == skorlamada_kalan


def test_a_null_category_never_reaches_the_model():
    """Kategorik bos hucre gercek disa aktarimlarda siradan; model onu hic gormemeli."""
    engineered, _ = build_training_frame(_tiny_raw())
    for kolon in CAT_COLS:
        if kolon in engineered.columns:
            assert engineered[kolon].isnull().sum() == 0, kolon


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

    # Kalan satirlar S1, S2, S3, S4 (S5 kategorik boslugu yuzunden atildi).
    # Aylık study hours: [10.0, null] -> 10.0. Yıllık: [4.0, 8.0] -> 6.0.
    # _global, kalan her satirin medyani: [10.0, 4.0, 8.0] -> 8.0.
    assert learned["weekly_study_hours_actual"] == {
        "Aylık": 10.0,
        "Yıllık": 6.0,
        "_global": 8.0,
    }
    # Aylık satisfaction: [4.0, 3.0] -> 3.5. Yıllık: [null, 2.0] -> 2.0.
    # _global: [4.0, 3.0, 2.0] -> 3.0.
    assert learned["satisfaction_survey_score"] == {
        "Aylık": 3.5,
        "Yıllık": 2.0,
        "_global": 3.0,
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


# --- B-21: temporal validity of the contact columns --------------------------
def test_the_audited_out_columns_are_not_model_features():
    """The audit's conclusion, as a config assertion (docs/LEAKAGE_AUDIT.md)."""
    assert set(AUDITED_OUT_FEATURES) & set(FEATURES) == set()
    for column in ("mentor_contact_freq_per_month", "days_since_last_contact",
                   "days_to_next_exam"):
        assert column in AUDITED_OUT_FEATURES
        assert column not in FEATURES
        assert column not in RAW_FEATURE_COLUMNS
        assert column not in SERVING_REQUIRED_COLUMNS


def test_audited_out_columns_are_dropped_from_a_raw_frame(raw_df):
    """A client export still sends them; they must not reach the model or the
    validation gate, which rejects any column outside the feature set."""
    assert set(AUDITED_OUT_FEATURES) <= set(raw_df.columns)  # they ARE in the export
    cleaned = drop_audited_out_columns(raw_df)
    assert set(AUDITED_OUT_FEATURES) & set(cleaned.columns) == set()
    # Nothing else is touched - same rows, and every other column still there.
    assert len(cleaned) == len(raw_df)
    assert set(raw_df.columns) - set(cleaned.columns) == set(AUDITED_OUT_FEATURES)


def test_drop_audited_out_columns_never_drops_a_readmitted_feature(raw_df, monkeypatch):
    """Re-admitting a column is one edit to config.FEATURES; the drop must yield to
    it rather than quietly removing a column the model is now trained on."""
    monkeypatch.setattr(
        features_module, "FEATURES", list(FEATURES) + ["days_since_last_contact"]
    )
    cleaned = drop_audited_out_columns(raw_df)
    assert "days_since_last_contact" in cleaned.columns
    assert "days_to_next_exam" not in cleaned.columns  # still parked


def test_the_engineered_training_frame_is_exactly_the_model_input(raw_df):
    """What the drop is FOR: validate() insists the frame is exactly
    STUDENT_INFO + FEATURES + TARGET_FEATURE, so a parked column left in the frame
    would fail the training run."""
    engineered, _learned = build_training_frame(raw_df)
    assert set(engineered.columns) == set(STUDENT_INFO) | set(FEATURES) | {TARGET_FEATURE}


def test_the_serving_frame_is_exactly_the_model_input(daily_df):
    engineered = build_serving_frame(daily_df, LEARNED_IMPUTATION)
    assert set(FEATURES) <= set(engineered.columns)
    assert set(AUDITED_OUT_FEATURES) & set(engineered.columns) == set()


def test_no_lag_is_applied_when_the_switch_is_off(raw_df):
    """0 is the shipped setting: the synthetic snapshot cannot support a lag."""
    assert CONTACT_LAG_DAYS == 0
    pd.testing.assert_frame_equal(apply_contact_lag(raw_df, 0), raw_df)
    pd.testing.assert_frame_equal(apply_contact_lag(raw_df, -5), raw_df)


def test_a_lag_on_a_feature_set_without_contact_columns_is_a_no_op(raw_df):
    """The shipped FEATURES has no contact column, so even a lag that is switched on
    has nothing to require of the export."""
    assert not set(CONTACT_FEATURES) & set(FEATURES)
    pd.testing.assert_frame_equal(apply_contact_lag(raw_df, 30), raw_df)


def test_a_lag_uses_the_window_start_column_and_removes_it(monkeypatch):
    """The real-client path: the export supplies the pre-window value per row."""
    monkeypatch.setattr(
        features_module, "FEATURES", list(FEATURES) + ["days_since_last_contact"]
    )
    frame = pd.DataFrame(
        {
            "student_id": ["S1", "S2"],
            "days_since_last_contact": [2, 3],            # at scoring time
            "days_since_last_contact_at_window_start": [41, 60],  # before the window
        }
    )
    lagged = apply_contact_lag(frame, 30)

    assert lagged["days_since_last_contact"].tolist() == [41, 60]
    assert "days_since_last_contact_at_window_start" not in lagged.columns
    assert frame["days_since_last_contact"].tolist() == [2, 3]  # input untouched


def test_a_lag_without_a_window_start_column_raises_rather_than_faking_one(monkeypatch):
    """The point of the mechanism. A lag that silently did not happen is worse than
    no lag: the metrics then look like a fixed model."""
    monkeypatch.setattr(
        features_module, "FEATURES", list(FEATURES) + ["days_since_last_contact"]
    )
    frame = pd.DataFrame({"student_id": ["S1"], "days_since_last_contact": [2]})

    with pytest.raises(ValueError) as error:
        apply_contact_lag(frame, 30)

    message = str(error.value)
    assert "days_since_last_contact_at_window_start" in message
    # and it did not quietly shift the scoring-time value by the lag instead
    assert frame["days_since_last_contact"].tolist() == [2]


# --- trend features -----------------------------------------------------------
_AS_OF = "2026-09-08"
_TREND_CFG = {"program_adherence_rate": [7, 28]}


def _trend_inputs():
    current = pd.DataFrame(
        {"student_id": ["A", "B", "C"], "program_adherence_rate": [0.60, 0.40, 0.90]}
    )
    history = pd.DataFrame(
        {
            "student_id": ["A", "A", "B"],
            "as_of_date": pd.to_datetime(["2026-09-01", "2026-08-11", "2026-09-01"]),
            "program_adherence_rate": [0.50, 0.80, 0.40],
        }
    )
    return current, history


def test_trend_delta_is_today_minus_the_value_n_days_ago():
    current, history = _trend_inputs()
    out = add_trend_features(current, history, _AS_OF, _TREND_CFG).set_index("student_id")

    assert out.loc["A", "program_adherence_rate_delta_7d"] == pytest.approx(0.10)
    assert out.loc["A", "program_adherence_rate_delta_28d"] == pytest.approx(-0.20)
    assert out.loc["B", "program_adherence_rate_delta_7d"] == pytest.approx(0.0)


def test_missing_history_is_null_plus_a_missing_flag():
    current, history = _trend_inputs()
    out = add_trend_features(current, history, _AS_OF, _TREND_CFG).set_index("student_id")

    # B has no snapshot 28 days ago; C has no history at all.
    for student in ("B", "C"):
        assert math.isnan(out.loc[student, "program_adherence_rate_delta_28d"])
        assert out.loc[student, "program_adherence_rate_delta_28d_missing"] == 1
    assert out.loc["C", "program_adherence_rate_delta_7d_missing"] == 1
    assert out.loc["A", "program_adherence_rate_delta_7d_missing"] == 0


def test_no_history_at_all_gives_all_missing_without_raising():
    current, _ = _trend_inputs()
    out = add_trend_features(current, pd.DataFrame(), _AS_OF, _TREND_CFG)

    assert out["program_adherence_rate_delta_7d"].isna().all()
    assert (out["program_adherence_rate_delta_7d_missing"] == 1).all()


def test_an_empty_trend_config_changes_nothing():
    current, history = _trend_inputs()
    pd.testing.assert_frame_equal(add_trend_features(current, history, _AS_OF, {}), current)


def test_the_lookup_takes_the_latest_snapshot_inside_the_tolerance_window():
    target = pd.Timestamp(_AS_OF) - pd.Timedelta(days=7)
    history = pd.DataFrame(
        {
            "student_id": ["A", "A", "A"],
            "as_of_date": [
                target - pd.Timedelta(days=TREND_TOLERANCE_DAYS + 1),  # too old: ignored
                target - pd.Timedelta(days=TREND_TOLERANCE_DAYS),      # oldest allowed
                target - pd.Timedelta(days=1),                         # latest allowed: wins
            ],
            "program_adherence_rate": [0.10, 0.20, 0.30],
        }
    )
    current = pd.DataFrame({"student_id": ["A"], "program_adherence_rate": [0.50]})

    out = add_trend_features(current, history, _AS_OF, {"program_adherence_rate": [7]})

    assert out.loc[0, "program_adherence_rate_delta_7d"] == pytest.approx(0.20)


def test_trend_does_not_touch_the_callers_frame():
    current, history = _trend_inputs()
    before = current.copy()
    add_trend_features(current, history, _AS_OF, _TREND_CFG)
    pd.testing.assert_frame_equal(current, before)


def test_trend_columns_are_model_features_but_not_raw_columns():
    """They are computed here from history, so a raw export must not be asked for them."""
    assert all(column in FEATURES for column in TREND_COLUMNS)
    assert not set(TREND_COLUMNS) & set(RAW_FEATURE_COLUMNS)