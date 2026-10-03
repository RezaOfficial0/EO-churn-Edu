"""scripts/make_synthetic_history.py - the fake history the backtest is demonstrated on.

Two kinds of test here, and the first kind matters more than the second.

**It must be impossible to mistake the output for real data.** The filenames, every
row of both CSVs, the mapping JSON and the OKUBENI file all have to say SENTETIK,
and `scripts/backtest.py` has to pick the marker up and refuse to print a figure
without a banner over it. A single number from these files shown to an investor is
the one failure this script could actually cause.

**The generated history must be a history**, i.e. internally consistent in time: no
row dated on or after the day a student left, tenure that counts up rather than
down, and churn dates that leave room for at least two observations beforehand.
"""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from config import SYNTHETIC_MARKER_COLUMN, SYNTHETIC_MARKER_VALUE, TARGET_FEATURE

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "make_synthetic_history.py"
_spec = importlib.util.spec_from_file_location("make_synthetic_history", _PATH)
maker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(maker)

_BACKTEST_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backtest.py"
_bt_spec = importlib.util.spec_from_file_location("backtest_for_synth", _BACKTEST_PATH)
backtest = importlib.util.module_from_spec(_bt_spec)
_bt_spec.loader.exec_module(backtest)

PERIOD_END = pd.Timestamp("2026-09-30").date()
PERIOD_START = pd.Timestamp("2025-09-30").date()


@pytest.fixture(scope="module")
def generated(raw_df):
    """One generation run over the real snapshot, shared by every test in the file."""
    history, outcomes, stats = maker.build_history(
        raw_df,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        observation_step_days=14,
        drift_days=90,
        noise_fraction=0.10,
        seed=42,
    )
    return history, outcomes, stats


# --- 1. It must be impossible to mistake for real data ------------------------
def test_every_written_row_carries_the_synthetic_marker(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)

    for name in ("history", "outcomes"):
        frame = pd.read_csv(paths[name])
        assert SYNTHETIC_MARKER_COLUMN in frame.columns
        assert (frame[SYNTHETIC_MARKER_COLUMN] == SYNTHETIC_MARKER_VALUE).all()


def test_every_filename_says_sentetik(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    for path in paths.values():
        assert path.name.startswith("SENTETIK_"), path.name


def test_the_mapping_and_the_notice_both_carry_the_warning(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)

    mapping = json.loads(paths["mapping"].read_text(encoding="utf-8"))
    assert "SENTETIK" in mapping["_notice"]

    notice = paths["notice"].read_text(encoding="utf-8")
    assert "GERCEK MUSTERI VERISI DEGIL" in notice
    assert "scripts/backtest.py" in notice
    # The ceiling on any lead time these files can show has to be written down next
    # to them, or the first person to quote one will quote it as a property of the
    # model rather than of the generator.
    assert str(stats["drift_days"]) in notice


def test_the_backtest_detects_the_marker_and_refuses_to_be_quiet(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    assert backtest.detect_synthetic(
        pd.read_csv(paths["history"], nrows=5), pd.read_csv(paths["outcomes"], nrows=5)
    )


# --- 2. The mapping it writes has to be the one the backtest needs -------------
def test_the_generated_files_load_through_the_backtests_own_contract(generated, tmp_path):
    """The generator's output and the backtest's input contract must agree exactly.

    The export uses the PROSPECT's column names and a dd.mm.yyyy date on purpose, so
    this is also the only test that exercises the mapping layer against a file
    somebody else's tool wrote.
    """
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    mapping = backtest.load_mapping(paths["mapping"])

    frame, info = backtest.read_history(paths["history"], mapping, paths["mapping"])
    churn_dates, outcome_info = backtest.read_outcomes(
        paths["outcomes"], mapping, paths["mapping"]
    )

    assert info["students"] == stats["students_in_history"]
    assert outcome_info["churners"] == stats["churners"]
    assert len(info["features_used"]) >= backtest.BACKTEST_MIN_FEATURES
    # The audited-out columns are in the export and must not be model inputs.
    assert info["audited_out_columns_ignored"]
    assert not set(info["audited_out_columns_ignored"]) & set(info["features_used"])

    coverage = backtest.check_outcome_coverage(
        frame,
        churn_dates,
        history_path=paths["history"],
        outcomes_path=paths["outcomes"],
        allow_partial=False,
    )
    assert coverage["match_share"] == 1.0


def test_the_export_does_not_use_our_column_names_for_the_mapped_five(generated, tmp_path):
    """If it did, the mapping - the part most likely to break on a real client -
    would never be exercised before a client exercised it."""
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    columns = set(pd.read_csv(paths["history"], nrows=1).columns)

    assert "ogrenci_no" in columns
    assert "gozlem_tarihi" in columns
    assert "student_id" not in columns
    assert "as_of_date" not in columns


def test_dates_are_written_in_the_clients_format_not_iso(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    first = pd.read_csv(paths["history"], nrows=1)["gozlem_tarihi"].iloc[0]
    assert pd.to_datetime(first, format=maker.CLIENT_DATE_FORMAT)
    with pytest.raises(ValueError):
        pd.to_datetime(first, format="%Y-%m-%d")


# --- 3. The history has to be internally consistent in time -------------------
def test_no_row_is_dated_on_or_after_the_day_the_student_left(generated):
    history, outcomes, _stats = generated
    churn = outcomes.set_index("student_id")["churn_date"]
    joined = history.assign(
        churn_date=pd.to_datetime(history["student_id"].map(churn))
    )
    gone = joined[joined["churn_date"].notna()]
    assert (pd.to_datetime(gone["as_of_date"]) < gone["churn_date"]).all()


def test_every_churner_has_at_least_two_observations_before_leaving(generated):
    history, outcomes, _stats = generated
    churners = set(outcomes.loc[outcomes["churned"] == maker.CHURN_TRUE_VALUE, "student_id"])
    counts = history[history["student_id"].isin(churners)].groupby("student_id").size()
    assert counts.min() >= 2


def test_tenure_counts_up_and_is_never_negative(generated):
    history, _outcomes, _stats = generated
    ordered = history.sort_values(["student_id", "as_of_date"])
    deltas = ordered.groupby("student_id")["tenure_months"].diff().dropna()
    assert (deltas >= 0).all()
    assert (history["tenure_months"] >= 0).all()


def test_churners_drift_towards_their_snapshot_values_as_they_approach_leaving(
    generated, raw_df
):
    """The signal the generator puts in, stated as a measurement rather than a claim.

    A churner's `program_adherence_rate` must be closer to the non-churner average
    early in their history than it is on their last observation. If this stops
    holding, the demo backtest measures nothing and the lead-time figure is noise.
    """
    history, outcomes, _stats = generated
    churners = set(outcomes.loc[outcomes["churned"] == maker.CHURN_TRUE_VALUE, "student_id"])
    healthy_mean = raw_df.loc[
        raw_df[TARGET_FEATURE] == 0, "program_adherence_rate"
    ].mean()

    rows = history[history["student_id"].isin(churners)].sort_values(
        ["student_id", "as_of_date"]
    )
    # Only students with a long enough history to have drifted at all.
    long_enough = rows.groupby("student_id").filter(lambda g: len(g) >= 7)
    first = long_enough.groupby("student_id").head(1)["program_adherence_rate"]
    last = long_enough.groupby("student_id").tail(1)["program_adherence_rate"]

    assert abs(first.mean() - healthy_mean) < abs(last.mean() - healthy_mean)


def test_nulls_in_the_snapshot_stay_null_for_that_student_in_every_row(generated, raw_df):
    """So the backtest's imputation path is actually taken rather than assumed."""
    history, _outcomes, _stats = generated
    missing = set(
        raw_df.loc[raw_df["satisfaction_survey_score"].isna(), "student_id"]
    )
    rows = history[history["student_id"].isin(missing)]
    assert len(rows)
    assert rows["satisfaction_survey_score"].isna().all()


def test_integer_features_are_written_as_whole_numbers(generated, tmp_path):
    history, outcomes, stats = generated
    paths = maker.write_files(history, outcomes, stats, tmp_path)
    text = paths["history"].read_text(encoding="utf-8").splitlines()
    header = text[0].split(",")
    column = header.index("late_response_count_30d")
    values = [line.split(",")[column] for line in text[1:50]]
    assert all("." not in value for value in values), values[:5]


def test_the_outcomes_file_covers_every_student_exactly_once(generated):
    _history, outcomes, stats = generated
    assert len(outcomes) == stats["students"]
    assert outcomes["student_id"].is_unique
    assert set(outcomes["churned"]) <= {maker.CHURN_TRUE_VALUE, maker.CHURN_FALSE_VALUE}


# --- 4. It refuses a snapshot it cannot use -----------------------------------
def test_a_snapshot_with_no_churn_column_is_refused(raw_df):
    with pytest.raises(maker.SnapshotError) as error:
        maker.build_history(
            raw_df.drop(columns=[TARGET_FEATURE]),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            observation_step_days=14,
            drift_days=90,
            noise_fraction=0.1,
            seed=1,
        )
    assert f"no {TARGET_FEATURE!r} column" in str(error.value)


def test_a_snapshot_with_no_enrolment_date_is_refused(raw_df):
    with pytest.raises(maker.SnapshotError) as error:
        maker.build_history(
            raw_df.drop(columns=[maker.ENROLMENT_COLUMN]),
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            observation_step_days=14,
            drift_days=90,
            noise_fraction=0.1,
            seed=1,
        )
    assert "when each student first appears" in str(error.value)


def test_a_snapshot_with_no_churners_is_refused(raw_df):
    no_churn = raw_df.copy()
    no_churn[TARGET_FEATURE] = 0
    with pytest.raises(maker.SnapshotError) as error:
        maker.build_history(
            no_churn,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            observation_step_days=14,
            drift_days=90,
            noise_fraction=0.1,
            seed=1,
        )
    assert "no churners at all" in str(error.value)


def test_generation_is_reproducible_for_a_given_seed(raw_df):
    small = raw_df.head(200)
    kwargs = {
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "observation_step_days": 28,
        "drift_days": 90,
        "noise_fraction": 0.1,
        "seed": 3,
    }
    first, first_outcomes, _ = maker.build_history(small, **kwargs)
    second, second_outcomes, _ = maker.build_history(small, **kwargs)
    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(first_outcomes, second_outcomes)


def test_observation_dates_are_built_backwards_from_the_last_one():
    dates = maker.observation_dates(
        pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-01"), step_days=14, jitter=3
    )
    assert dates[-1] == pd.Timestamp("2026-02-26")  # last minus the jitter
    assert all(
        (b - a).days == 14 for a, b in zip(dates, dates[1:])
    )
    assert dates[0] >= pd.Timestamp("2026-01-01")


def test_clipping_keeps_a_generated_column_inside_the_products_own_bounds():
    import numpy as np

    values = maker._clip_and_round(np.array([-5.0, 0.5, 9.0]), "program_adherence_rate")
    assert values.tolist() == [0.0, 0.5, 1.0]
    counts = maker._clip_and_round(np.array([2.4, 2.6]), "late_response_count_30d")
    assert counts.tolist() == [2.0, 3.0]
