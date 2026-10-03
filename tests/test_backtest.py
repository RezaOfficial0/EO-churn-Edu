"""scripts/backtest.py - the retrospective backtest offered to a prospect.

Two things are worth pinning here above everything else.

1. **The time-ordering guarantee.** A backtest that sees the future reports a
   BETTER number than a correct one, so nothing downstream will ever complain about
   the bug. `leakage_history()` below builds a history in which one feature column
   is pure noise everywhere the model is allowed to look and a perfect copy of the
   future label everywhere it is not. A correct run therefore scores about the base
   rate; a leaking one scores ~1.0.
   `test_the_construction_would_expose_a_leak` trains deliberately across the
   embargo on the same data and asserts that it DOES score ~1.0 - without that, the
   leakage test could be passing because the construction is inert.

2. **The input validation.** Every message a prospect's export can trigger is
   asserted to name the file, the column and the fix, because the person reading it
   is reading it at 23:00 with a customer waiting.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.model.evaluate import precision_at_k
from src.model.model import build_model

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backtest.py"
_spec = importlib.util.spec_from_file_location("backtest", _PATH)
backtest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backtest)

BacktestInputError = backtest.BacktestInputError
TemporalLeakageError = backtest.TemporalLeakageError

ORIGIN = pd.Timestamp("2026-06-01")
WINDOW = 30
# Three numeric features, no categoricals: the leakage test is about the time axis
# and a categorical column would only add CatBoost noise to it.
THREE_FEATURES = [
    "program_adherence_rate",
    "weekly_study_hours_planned",
    "trial_exam_avg_net",
]
ORACLE = THREE_FEATURES[0]


# --- Fixtures ----------------------------------------------------------------
def leakage_history(seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """A history in which leakage, and only leakage, is learnable.

    Three groups of students:
      PAST  (40)  leave before the origin  -> the training slice's positives
      FUT   (30)  leave inside (origin, origin + 30]  -> the positives being predicted
      STAY  (270) never leave

    Observation dates. Everyone gets origin-75 and origin-60, which are the only
    dates the embargo lets a model fit on (as_of + 30 <= origin). Every surviving
    student also gets origin-25, origin-18 and origin-10, all of which sit INSIDE
    the embargo.

    `program_adherence_rate` is uniform noise on the two clean dates and, on all
    three embargoed dates, "this student leaves within 60 days" - a fact that was
    not knowable then. So the column is worthless to a model that obeys the embargo
    and an oracle to one that does not, and the top of the leaky model's list is the
    30 students who actually left.

    Three embargoed dates rather than one on purpose: the fold holds its latest
    dates back for calibration, so with a single embargoed date a leaking
    implementation would put the oracle rows in the calibration half and the test
    would pass for the wrong reason.
    """
    rng = np.random.default_rng(seed)
    churn_dates: dict[str, pd.Timestamp] = {}
    for index in range(40):
        churn_dates[f"PAST{index:03d}"] = ORIGIN - pd.Timedelta(
            days=int(rng.integers(35, 59))
        )
    for index in range(30):
        churn_dates[f"FUT{index:03d}"] = ORIGIN + pd.Timedelta(
            days=int(rng.integers(1, 31))
        )
    survivors = [f"FUT{i:03d}" for i in range(30)] + [f"STAY{i:03d}" for i in range(270)]
    everyone = list(churn_dates) + [s for s in survivors if s not in churn_dates]

    CLEAN_OFFSETS = (75, 60)
    EMBARGOED_OFFSETS = (25, 18, 10)

    rows = []
    for student_id in everyone:
        churn_date = churn_dates.get(student_id, pd.NaT)
        for offset in CLEAN_OFFSETS:
            as_of = ORIGIN - pd.Timedelta(days=offset)
            if pd.notna(churn_date) and churn_date <= as_of:
                continue
            rows.append(_leakage_row(student_id, as_of, float(rng.random()), rng))
        if student_id not in survivors:
            continue
        for offset in EMBARGOED_OFFSETS:
            as_of = ORIGIN - pd.Timedelta(days=offset)
            # The leak: tomorrow's answer, written into a feature column.
            leaves_soon = float(
                pd.notna(churn_date) and (churn_date - as_of).days <= 60
            )
            rows.append(_leakage_row(student_id, as_of, leaves_soon, rng))

    history = pd.DataFrame(rows)
    churn_series = pd.Series(
        {student_id: churn_dates.get(student_id, pd.NaT) for student_id in everyone},
        dtype="datetime64[ns]",
    )
    history["churn_date"] = history["student_id"].map(churn_series)
    return history, churn_series


def _leakage_row(student_id, as_of, oracle_value, rng) -> dict:
    return {
        "student_id": student_id,
        "as_of_date": as_of,
        ORACLE: oracle_value,
        "weekly_study_hours_planned": float(rng.random() * 10),
        "trial_exam_avg_net": float(rng.random() * 50),
        "monthly_value_try": 1000.0,
    }


def _fold(history: pd.DataFrame, **overrides):
    kwargs = {
        "feature_columns": THREE_FEATURES,
        "churn_window_days": WINDOW,
        "active_window_days": 20,
        "k": 20,
        "flag_rule": "capacity",
        "baseline_column": None,
        "min_train_rows": 200,
        "min_train_churners": 10,
        "val_date_fraction": 0.25,
    }
    kwargs.update(overrides)
    return backtest.run_fold(history, ORIGIN, **kwargs)


# --- 1. The time-ordering guarantee ------------------------------------------
def test_run_fold_does_not_let_a_future_row_influence_a_past_decision():
    """THE test. A leak here would show up as an impossibly good score.

    `ORACLE` is the future label on every row inside the embargo and noise on every
    row outside it, and the base rate at the origin is 10%. A model that trained on
    the embargoed rows would read the oracle straight off the scoring frame and get
    precision@20 = 1.0.
    """
    history, _ = leakage_history()
    fold = _fold(history)

    assert fold["status"] == "ok"
    assert fold["scored"]["base_rate"] == pytest.approx(0.10, abs=0.02)
    assert fold["scored"]["precision_at_k"] <= 0.30, (
        "precision@20 is far above the base rate on data whose only strong signal "
        "is inside the embargo - a future row reached the model"
    )
    assert fold["scored"]["roc_auc"] < 0.80


def test_the_construction_would_expose_a_leak():
    """Guard on the guard: the same data DOES score ~1.0 when the embargo is removed.

    Without this, the test above could be passing because `leakage_history` carries
    no learnable leak at all, and the one assertion the whole report rests on would
    be vacuous.
    """
    history, _ = leakage_history()
    leaky = history[history["as_of_date"] <= ORIGIN]
    labels = backtest.label_rows(
        leaky["as_of_date"], leaky["churn_date"], churn_window_days=WINDOW
    )
    prep = backtest.fit_preprocessor(leaky, THREE_FEATURES)

    model = build_model(cat_features=[])
    model.fit(backtest.apply_preprocessor(leaky, prep), labels, verbose=False)

    scoring = backtest.select_scoring_rows(history, ORIGIN, active_window_days=20)
    truth = backtest.label_rows(
        ORIGIN, scoring["churn_date"], churn_window_days=WINDOW
    )
    scores = model.predict_proba(backtest.apply_preprocessor(scoring, prep))[:, 1]

    assert precision_at_k(truth, scores, 20) >= 0.90


def test_removing_the_embargo_makes_the_fold_score_perfectly(monkeypatch):
    """The sensitivity of the assertion above, pinned end to end.

    With BOTH the embargo and the `_assert_only_past` guard removed - the shape a
    refactor that "simplified" the filter would take - the same fold scores
    precision@20 = 1.0 against a 10% base rate. So
    `test_run_fold_does_not_let_a_future_row_influence_a_past_decision` is a real
    assertion and not a tautology about a fixture with no signal in it.
    """
    history, _ = leakage_history()
    monkeypatch.setattr(backtest, "_assert_only_past", lambda *a, **k: None)
    monkeypatch.setattr(
        backtest,
        "select_training_rows",
        lambda frame, origin, **_: frame[frame["as_of_date"] <= origin].reset_index(
            drop=True
        ),
    )
    fold = _fold(history)
    assert fold["scored"]["precision_at_k"] >= 0.90
    assert fold["scored"]["roc_auc"] >= 0.95


def test_select_training_rows_stops_one_whole_window_before_the_origin():
    history, _ = leakage_history()
    rows = backtest.select_training_rows(history, ORIGIN, churn_window_days=WINDOW)

    assert rows["as_of_date"].max() <= ORIGIN - pd.Timedelta(days=WINDOW)
    # The embargoed rows exist in the history and are the ones left out.
    assert (history["as_of_date"] > ORIGIN - pd.Timedelta(days=WINDOW)).any()
    assert set(rows["as_of_date"].unique()) == {
        ORIGIN - pd.Timedelta(days=75),
        ORIGIN - pd.Timedelta(days=60),
    }


def test_select_training_rows_drops_rows_dated_after_a_student_left():
    history = pd.DataFrame(
        {
            "student_id": ["A", "A"],
            "as_of_date": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-01")],
            "churn_date": [pd.Timestamp("2026-02-01")] * 2,
            "program_adherence_rate": [0.5, 0.5],
        }
    )
    rows = backtest.select_training_rows(
        history, pd.Timestamp("2026-06-01"), churn_window_days=WINDOW
    )
    assert rows["as_of_date"].tolist() == [pd.Timestamp("2026-01-01")]


def test_assert_only_past_raises_and_names_the_offending_date():
    dates = pd.Series([pd.Timestamp("2026-05-20")])
    with pytest.raises(TemporalLeakageError) as error:
        backtest._assert_only_past(
            dates, ORIGIN, churn_window_days=WINDOW, what="training rows"
        )
    message = str(error.value)
    assert "2026-05-20" in message
    assert "2026-05-02" in message  # the limit it had to be at or before
    assert "training rows" in message


def test_scoring_rows_never_describe_anything_after_the_origin():
    history, _ = leakage_history()
    rows = backtest.select_scoring_rows(history, ORIGIN, active_window_days=20)
    assert rows["as_of_date"].max() <= ORIGIN
    assert rows["student_id"].is_unique


def test_scoring_excludes_students_who_had_already_left():
    history, _ = leakage_history()
    rows = backtest.select_scoring_rows(history, ORIGIN, active_window_days=90)
    gone = {s for s in rows["student_id"] if s.startswith("PAST")}
    assert not gone


def test_imputation_medians_are_fitted_on_the_training_slice_only():
    """The per-row recipe obeys the same embargo as the model.

    A median taken over the whole history would carry the embargoed rows' values
    into the number used to fill the training slice - a smaller leak than a training
    row, and exactly as invisible.
    """
    history = pd.DataFrame(
        {
            "student_id": [f"S{i}" for i in range(6)],
            "as_of_date": [ORIGIN - pd.Timedelta(days=d) for d in (60, 60, 60, 5, 5, 5)],
            "churn_date": pd.NaT,
            "program_adherence_rate": [1.0, 1.0, np.nan, 99.0, 99.0, 99.0],
        }
    )
    train_rows = backtest.select_training_rows(
        history, ORIGIN, churn_window_days=WINDOW
    )
    prep = backtest.fit_preprocessor(train_rows, ["program_adherence_rate"])

    assert prep["medians"]["program_adherence_rate"] == 1.0
    assert "program_adherence_rate" in prep["flagged"]
    filled = backtest.apply_preprocessor(train_rows, prep)
    assert filled["program_adherence_rate"].tolist() == [1.0, 1.0, 1.0]
    assert filled["program_adherence_rate_missing"].tolist() == [0, 0, 1]


def test_run_fold_asserts_the_guarantee_on_the_frame_it_fits():
    """The guard is on the data, not on the call site: break the selection and it fires."""
    history, _ = leakage_history()
    original = backtest.select_training_rows
    try:
        # A plausible-looking bug: filter on the origin instead of the embargo.
        backtest.select_training_rows = lambda frame, origin, **_: frame[
            frame["as_of_date"] <= origin
        ].reset_index(drop=True)
        with pytest.raises(TemporalLeakageError):
            _fold(history)
    finally:
        backtest.select_training_rows = original


# --- 2. Walk-forward layout ---------------------------------------------------
def test_evaluation_origins_are_anchored_to_the_end_of_the_export():
    origins = backtest.evaluation_origins(
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-12-31"),
        churn_window_days=30,
        step_days=30,
        warmup_days=60,
    )
    assert origins[-1] == pd.Timestamp("2025-12-01")  # last - churn window
    assert origins[0] >= pd.Timestamp("2025-03-02")  # first + warmup
    gaps = {(b - a).days for a, b in zip(origins, origins[1:])}
    assert gaps == {30}


def test_evaluation_origins_refuses_a_history_that_is_too_short():
    with pytest.raises(BacktestInputError) as error:
        backtest.evaluation_origins(
            pd.Timestamp("2026-01-01"),
            pd.Timestamp("2026-02-11"),
            churn_window_days=30,
            step_days=30,
            warmup_days=60,
        )
    message = str(error.value)
    assert "41 day(s)" in message
    assert "--churn-window-days" in message


def test_label_rows_is_half_open_at_the_as_of_date():
    """A churn ON the as-of date is not something that row could have warned about."""
    as_of = pd.Series([pd.Timestamp("2026-01-10")] * 3)
    churn = pd.Series(
        [
            pd.Timestamp("2026-01-10"),  # same day - not a warning opportunity
            pd.Timestamp("2026-01-11"),  # inside
            pd.Timestamp("2026-02-10"),  # one day past the window
        ]
    )
    labels = backtest.label_rows(as_of, churn, churn_window_days=30)
    assert labels.tolist() == [0, 1, 0]


# --- 3. Lead time -------------------------------------------------------------
def _scored(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    frame["origin"] = pd.to_datetime(frame["origin"])
    frame["churn_date"] = pd.to_datetime(frame["churn_date"])
    for column in ("baseline_flagged", "label"):
        if column not in frame.columns:
            frame[column] = 0
    if "monthly_value_try" not in frame.columns:
        frame["monthly_value_try"] = 100.0
    frame["history_row"] = range(len(frame))
    return frame


def test_lead_time_is_measured_from_the_first_flag_not_the_last():
    scored = _scored(
        [
            {"student_id": "A", "origin": "2026-03-01", "churn_date": "2026-05-15",
             "flagged": 1, "label": 0},
            {"student_id": "A", "origin": "2026-04-01", "churn_date": "2026-05-15",
             "flagged": 1, "label": 0},
            {"student_id": "A", "origin": "2026-05-01", "churn_date": "2026-05-15",
             "flagged": 1, "label": 1},
        ]
    )
    table, info = backtest.lead_time_records(
        scored,
        first_origin=pd.Timestamp("2026-03-01"),
        last_origin=pd.Timestamp("2026-05-01"),
        churn_window_days=30,
        churn_dates=pd.Series({"A": pd.Timestamp("2026-05-15")}),
    )
    assert table["lead_days"].tolist() == [75]
    assert info["churners_with_a_scoring_opportunity"] == 1


def test_a_flag_raised_after_the_student_left_is_not_a_lead_time():
    scored = _scored(
        [
            {"student_id": "A", "origin": "2026-03-01", "churn_date": "2026-02-01",
             "flagged": 1, "label": 0},
        ]
    )
    table, info = backtest.lead_time_records(
        scored,
        first_origin=pd.Timestamp("2026-01-01"),
        last_origin=pd.Timestamp("2026-03-01"),
        churn_window_days=30,
        churn_dates=pd.Series({"A": pd.Timestamp("2026-02-01")}),
    )
    assert table.empty
    assert info["churners_never_scored_before_leaving"] == 1


def test_a_churner_we_never_scored_is_reported_rather_than_counted_as_a_miss():
    """Blaming the model for a gap in the client's own export would be dishonest."""
    scored = _scored(
        [
            {"student_id": "A", "origin": "2026-03-01", "churn_date": "2026-04-10",
             "flagged": 0, "label": 1},
        ]
    )
    churn_dates = pd.Series(
        {"A": pd.Timestamp("2026-04-10"), "GHOST": pd.Timestamp("2026-03-25")}
    )
    table, info = backtest.lead_time_records(
        scored,
        first_origin=pd.Timestamp("2026-02-01"),
        last_origin=pd.Timestamp("2026-03-01"),
        churn_window_days=45,
        churn_dates=churn_dates,
    )
    assert info["churners_in_measured_span"] == 2
    assert info["churners_with_a_scoring_opportunity"] == 1
    assert info["churners_never_scored_before_leaving"] == 1
    assert table["flagged"].tolist() == [0]


def test_distribution_reports_quartiles_and_ignores_the_students_never_flagged():
    distribution = backtest._distribution([10, 20, 30, 40, None, None])
    assert distribution["n"] == 4
    assert distribution["median"] == 25.0
    assert distribution["q1"] == 17.5
    assert distribution["q3"] == 32.5
    assert distribution["min"] == 10
    assert distribution["max"] == 40


def test_distribution_of_nothing_is_not_a_zero():
    assert backtest._distribution([None, None]) == {"n": 0}


# --- 4. Capacity ceiling ------------------------------------------------------
def test_oracle_coverage_is_bounded_by_the_capacity_spent():
    scored = _scored(
        [
            {"student_id": "A", "origin": "2026-03-01", "churn_date": "2026-03-20",
             "flagged": 0, "label": 1},
            {"student_id": "B", "origin": "2026-03-01", "churn_date": "2026-03-25",
             "flagged": 0, "label": 1},
            {"student_id": "C", "origin": "2026-03-01", "churn_date": "2026-03-28",
             "flagged": 0, "label": 1},
        ]
    )
    table = pd.DataFrame({"student_id": ["A", "B", "C"], "flagged": [0, 0, 0]})
    ceiling = backtest.oracle_coverage(scored, table, k=1, flag_rule="capacity")

    # One slot, one evaluation point, three churners: a perfect ranking covers one.
    assert ceiling["flag_slots_total"] == 1
    assert ceiling["oracle_coverage"] == pytest.approx(1 / 3)


def test_there_is_no_capacity_ceiling_under_the_threshold_rule():
    ceiling = backtest.oracle_coverage(
        _scored([{"student_id": "A", "origin": "2026-03-01",
                  "churn_date": "2026-03-20", "flagged": 1, "label": 1}]),
        pd.DataFrame({"student_id": ["A"], "flagged": [1]}),
        k=1,
        flag_rule="threshold",
    )
    assert ceiling["available"] is False


# --- 5. Degenerate folds ------------------------------------------------------
def test_a_window_with_no_active_student_is_skipped_with_the_reason():
    history, _ = leakage_history()
    # Nobody has an observation in the 2 days before the origin.
    fold = _fold(history, active_window_days=2)
    assert fold["status"] == "skipped"
    assert "nobody to score" in fold["reason"]


def test_a_fold_with_too_little_training_data_is_skipped_not_reported():
    history, _ = leakage_history()
    fold = _fold(history, min_train_rows=10_000)
    assert fold["status"] == "skipped"
    assert "minimum 10000" in fold["reason"]


def test_a_fold_with_too_few_training_churners_is_skipped():
    history, _ = leakage_history()
    fold = _fold(history, min_train_churners=10_000)
    assert fold["status"] == "skipped"
    assert "churner(s) in the training slice" in fold["reason"]


def test_a_window_with_no_churners_gives_no_auc_rather_than_an_error():
    truth = np.zeros(50, dtype=int)
    scores = np.linspace(0, 1, 50)
    metrics = backtest._fold_metrics(truth, scores, scores > 0.9, k=20)
    assert metrics["roc_auc"] is None
    assert metrics["pr_auc"] is None
    assert metrics["lift_at_k"] is None
    assert metrics["churners_in_window"] == 0
    assert metrics["precision"] == 0.0


def test_one_student_is_a_number_not_a_crash():
    truth = np.array([1])
    metrics = backtest._fold_metrics(truth, np.array([0.9]), np.array([True]), k=20)
    assert metrics["students_scored"] == 1
    assert metrics["k"] == 1
    assert metrics["precision"] == 1.0


def test_missed_breakdown_reads_the_students_own_history_row():
    """The breakdown is only honest if it reads the right rows.

    `history_row` is an index into the HISTORY frame, not into one fold's scored
    rows, and the two differ as soon as there is more than one evaluation point. A
    mix-up here produces a plausible-looking segment table about the wrong students,
    which is the most expensive kind of wrong: nothing looks broken.
    """
    history = pd.DataFrame(
        {
            "student_id": ["A", "B", "C", "D"],
            "plan_type": ["Aylık", "Aylık", "Yıllık", "Yıllık"],
            "tenure_months": [1.0, 2.0, 30.0, 40.0],
        }
    )
    # The two churners are history rows 2 and 3, not 0 and 1.
    table = pd.DataFrame(
        {
            "student_id": ["C", "D"],
            "flagged": [1, 0],
            "monthly_value_try": [100.0, 200.0],
            "last_scored_row": [2, 3],
        }
    )
    result = backtest.missed_breakdown(
        table, history, ["plan_type", "tenure_months"]
    )
    assert result["missed"] == 1
    assert result["caught"] == 1
    assert result["by_segment"]["plan_type"] == {
        "Yıllık": {"churners": 2, "missed": 1, "missed_rate": 0.5}
    }
    assert result["missed_monthly_value_try"] == 200.0


def test_missed_breakdown_says_so_when_there_is_no_evaluable_churner():
    result = backtest.missed_breakdown(
        pd.DataFrame(), pd.DataFrame(), THREE_FEATURES
    )
    assert result["missed"] == 0
    assert "no evaluable churner" in result["note"]


def test_bootstrap_over_an_empty_table_is_not_reportable():
    out = backtest.bootstrap_over_students(
        pd.DataFrame(), {"coverage": lambda t: 1.0}, resamples=10, seed=1
    )
    assert out["coverage"] == {"n": 0, "reportable": False}


def test_a_figure_measured_on_too_few_students_is_marked_as_noise():
    table = pd.DataFrame({"flagged": [1, 0, 1], "lead_days": [10, None, 20]})
    out = backtest.bootstrap_over_students(
        table,
        {"coverage": lambda t: float(t["flagged"].mean())},
        resamples=50,
        seed=1,
    )
    assert out["coverage"]["n"] == 3
    assert out["coverage"]["reportable"] is False


# --- 6. Input validation ------------------------------------------------------
HISTORY_CSV = "ogrenci_no,tarih,program_adherence_rate,tenure_months,trial_exam_avg_net\n"
MINIMAL_MAPPING = {
    "history": {"ogrenci_no": "student_id", "tarih": "as_of_date"},
    "outcomes": {"ogrenci_no": "student_id", "cikis": "churn_date"},
}


@pytest.fixture
def workspace(tmp_path):
    """Three valid files, so each test can break exactly one thing."""
    history = tmp_path / "history.csv"
    history.write_text(
        HISTORY_CSV
        + "\n".join(
            f"S{i},2026-0{1 + i % 3}-15,0.{i % 9 + 1},{i % 12},{40 + i % 10}"
            for i in range(10)
        )
        + "\n",
        encoding="utf-8",
    )
    outcomes = tmp_path / "outcomes.csv"
    outcomes.write_text(
        "ogrenci_no,cikis\n" + "\n".join(f"S{i}," for i in range(10)) + "\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps(MINIMAL_MAPPING), encoding="utf-8")
    return {"history": history, "outcomes": outcomes, "mapping": mapping, "dir": tmp_path}


def _read_history(workspace):
    return backtest.read_history(
        workspace["history"],
        json.loads(workspace["mapping"].read_text(encoding="utf-8")),
        workspace["mapping"],
    )


def test_the_valid_workspace_loads(workspace):
    frame, info = _read_history(workspace)
    assert info["rows"] == 10
    assert set(info["features_used"]) == {
        "program_adherence_rate",
        "tenure_months",
        "trial_exam_avg_net",
    }
    assert frame["as_of_date"].dtype.kind == "M"


def test_a_missing_mapping_file_says_what_to_create(tmp_path):
    with pytest.raises(BacktestInputError) as error:
        backtest.load_mapping(tmp_path / "nope.json")
    message = str(error.value)
    assert "mapping file not found" in message
    assert "--contract" in message


def test_broken_json_names_the_line_and_the_usual_cause(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text('{"history": {"a": "student_id",}}', encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        backtest.load_mapping(path)
    message = str(error.value)
    assert "is not valid JSON" in message
    assert "line 1" in message
    assert "trailing comma" in message


def test_an_unknown_mapping_key_is_a_typo_not_a_comment(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"histroy": {}, "_note": "fine"}), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        backtest.load_mapping(path)
    assert "unknown key(s): ['histroy']" in str(error.value)


def test_a_mapping_section_that_is_not_an_object_is_refused(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"history": ["student_id"]}), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        backtest.load_mapping(path)
    assert 'must be an object mapping THEIR column name to OUR column name' in str(
        error.value
    )


def test_a_mapping_onto_an_unknown_column_lists_what_is_known(workspace):
    mapping = {"history": {"ogrenci_no": "student_id", "tarih": "as_of_dat"}}
    workspace["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    message = str(error.value)
    assert '"as_of_dat", which is not a column this backtest knows' in message
    assert "as_of_date" in message
    assert "RIGHT of the mapping" in message


def test_a_mapping_onto_a_column_we_compute_is_refused(workspace):
    mapping = {
        "history": {
            "ogrenci_no": "student_id",
            "tarih": "as_of_date",
            "program_adherence_rate": "satisfaction_missing",
        }
    }
    workspace["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert "which this backtest COMPUTES from your data" in str(error.value)


def test_a_mapping_pointing_at_a_column_that_is_not_in_the_file(workspace):
    mapping = {"history": {"ogrenci_numarasi": "student_id", "tarih": "as_of_date"}}
    workspace["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    message = str(error.value)
    assert "['ogrenci_numarasi'] is mapped but is not a column in" in message
    assert "ogrenci_no" in message  # the file's real columns are listed
    assert "LEFT of the mapping" in message


def test_two_of_their_columns_cannot_share_one_of_ours(workspace):
    mapping = {
        "history": {
            "ogrenci_no": "student_id",
            "tarih": "as_of_date",
            "tenure_months": "as_of_date",
        }
    }
    workspace["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert '"as_of_date" is the target of 2 columns' in str(error.value)


def test_a_rename_that_collides_with_an_existing_column_is_refused(workspace):
    mapping = {
        "history": {"ogrenci_no": "student_id", "tarih": "as_of_date",
                    "trial_exam_avg_net": "tenure_months"}
    }
    workspace["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert "already has a column with that name" in str(error.value)


def test_a_missing_required_column_says_exactly_what_to_add(workspace):
    workspace["mapping"].write_text(
        json.dumps({"history": {"ogrenci_no": "student_id"}}), encoding="utf-8"
    )
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    message = str(error.value)
    assert "missing required column(s): ['as_of_date']" in message
    assert '"<your column>": "as_of_date"' in message
    assert "mapping.json" in message


def test_an_unparseable_date_names_the_rows_and_offers_date_format(workspace):
    workspace["history"].write_text(
        HISTORY_CSV
        + "S0,2026-01-15,0.5,3,44\nS1,32/13/2026,0.6,4,45\nS2,gecen hafta,0.7,5,46\n",
        encoding="utf-8",
    )
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    message = str(error.value)
    assert "2 of 3 value(s) could not be read as a date" in message
    assert "row 2 '32/13/2026'" in message
    assert "row 3 'gecen hafta'" in message
    assert '"date_format": "%d.%m.%Y"' in message


def test_a_stated_date_format_that_does_not_match_says_so(workspace):
    workspace["mapping"].write_text(
        json.dumps({**MINIMAL_MAPPING, "date_format": "%d.%m.%Y"}), encoding="utf-8"
    )
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert 'the mapping states "date_format": "%d.%m.%Y"' in str(error.value)


def test_a_history_row_with_no_date_at_all_is_refused(workspace):
    workspace["history"].write_text(
        HISTORY_CSV + "S0,2026-01-15,0.5,3,44\nS1,,0.6,4,45\n", encoding="utf-8"
    )
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert "1 row(s) have no date at all" in str(error.value)
    assert "cannot be placed in time" in str(error.value)


def test_an_empty_history_file_is_refused(workspace):
    workspace["history"].write_text(HISTORY_CSV, encoding="utf-8")
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    assert "has a header but no data rows" in str(error.value)


def test_too_few_feature_columns_refuses_rather_than_training_on_nothing(workspace):
    workspace["history"].write_text(
        "ogrenci_no,tarih,tenure_months\nS0,2026-01-15,3\nS1,2026-02-15,4\n",
        encoding="utf-8",
    )
    with pytest.raises(BacktestInputError) as error:
        _read_history(workspace)
    message = str(error.value)
    assert "carries 1 of the model's feature columns" in message
    assert "at least 3" in message


def test_a_churned_flag_with_no_date_is_refused(workspace):
    workspace["outcomes"].write_text(
        "ogrenci_no,cikis,ayrildi\nS0,,Evet\nS1,2026-03-01,Evet\n", encoding="utf-8"
    )
    workspace["mapping"].write_text(
        json.dumps(
            {
                **MINIMAL_MAPPING,
                "outcomes": {
                    "ogrenci_no": "student_id",
                    "cikis": "churn_date",
                    "ayrildi": "churned",
                },
                "churn_true_values": ["Evet"],
                "churn_false_values": ["Hayir"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BacktestInputError) as error:
        backtest.read_outcomes(
            workspace["outcomes"],
            json.loads(workspace["mapping"].read_text(encoding="utf-8")),
            workspace["mapping"],
        )
    message = str(error.value)
    assert "1 row(s) are marked churned but carry no churn_date" in message
    assert "student 'S0'" in message
    assert "how many days EARLY" in message


def test_an_ambiguous_churn_flag_value_is_refused(workspace):
    workspace["outcomes"].write_text(
        "ogrenci_no,cikis,ayrildi\nS0,2026-03-01,belki\n", encoding="utf-8"
    )
    workspace["mapping"].write_text(
        json.dumps(
            {
                **MINIMAL_MAPPING,
                "outcomes": {
                    "ogrenci_no": "student_id",
                    "cikis": "churn_date",
                    "ayrildi": "churned",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BacktestInputError) as error:
        backtest.read_outcomes(
            workspace["outcomes"],
            json.loads(workspace["mapping"].read_text(encoding="utf-8")),
            workspace["mapping"],
        )
    message = str(error.value)
    assert "are neither true nor false" in message
    assert '"churn_true_values"' in message


def test_outcomes_without_a_flag_column_read_the_date_as_the_event(workspace):
    workspace["outcomes"].write_text(
        "ogrenci_no,cikis\nS0,2026-03-01\nS1,\n", encoding="utf-8"
    )
    churn_dates, info = backtest.read_outcomes(
        workspace["outcomes"], MINIMAL_MAPPING, workspace["mapping"]
    )
    assert info["churners"] == 1
    assert churn_dates["S0"] == pd.Timestamp("2026-03-01")
    assert pd.isna(churn_dates["S1"])


def test_a_churners_only_outcomes_file_is_accepted(workspace):
    """docs/VERI_TALEBI.md asks the prospect for one row per CHURNER only.

    In that shape most history students are legitimately absent from the outcomes
    file, so gating on "share of history students found in outcomes" would reject
    exactly the file we asked for.
    """
    history, _ = _read_history(workspace)
    churn_dates = pd.Series(
        {"S1": pd.Timestamp("2026-03-01"), "S4": pd.Timestamp("2026-04-01")},
        dtype="datetime64[ns]",
    )
    coverage = backtest.check_outcome_coverage(
        history,
        churn_dates,
        history_path=workspace["history"],
        outcomes_path=workspace["outcomes"],
        allow_partial=False,
    )
    assert coverage["outcomes_file_shape"] == "churners_only"
    assert coverage["share_of_outcome_ids"] == 1.0
    assert coverage["share_of_history_students"] == 0.2
    assert coverage["unmatched_treated_as_never_churned"] == 8


def test_an_id_mismatch_is_still_caught_in_a_churners_only_file(workspace):
    """The permissive shape must not also make the id-mismatch check toothless."""
    history, _ = _read_history(workspace)
    churn_dates = pd.Series(
        {"100": pd.Timestamp("2026-03-01"), "101": pd.Timestamp("2026-04-01")},
        dtype="datetime64[ns]",
    )
    with pytest.raises(BacktestInputError) as error:
        backtest.check_outcome_coverage(
            history,
            churn_dates,
            history_path=workspace["history"],
            outcomes_path=workspace["outcomes"],
            allow_partial=False,
        )
    message = str(error.value)
    assert "outcomes file lists only churners" in message
    assert "0 id(s) in common" in message
    assert "--allow-partial-outcomes" in message


def test_an_id_mismatch_between_the_two_files_is_caught_not_reported_as_zero(workspace):
    history, _ = _read_history(workspace)
    churn_dates = pd.Series(
        {"100": pd.Timestamp("2026-03-01"), "101": pd.NaT}, dtype="datetime64[ns]"
    )
    with pytest.raises(BacktestInputError) as error:
        backtest.check_outcome_coverage(
            history,
            churn_dates,
            history_path=workspace["history"],
            outcomes_path=workspace["outcomes"],
            allow_partial=False,
        )
    message = str(error.value)
    assert "share of the history's students found in the outcomes file is 0.0%" in message
    assert "student-id mismatch" in message
    assert "--allow-partial-outcomes" in message


def test_allow_partial_outcomes_accepts_the_mismatch_on_purpose(workspace):
    history, _ = _read_history(workspace)
    churn_dates = pd.Series({"100": pd.NaT}, dtype="datetime64[ns]")
    coverage = backtest.check_outcome_coverage(
        history,
        churn_dates,
        history_path=workspace["history"],
        outcomes_path=workspace["outcomes"],
        allow_partial=True,
    )
    assert coverage["matched_students"] == 0
    assert coverage["unmatched_treated_as_never_churned"] == 10


def test_monthly_value_is_derived_from_the_plan_price(workspace):
    workspace["history"].write_text(
        "ogrenci_no,tarih,program_adherence_rate,tenure_months,trial_exam_avg_net,"
        "monthly_fee_try,plan_type\n"
        "S0,2026-01-15,0.5,3,44,16730,Yıllık\n"
        "S1,2026-02-15,0.6,4,45,1800,Aylık\n",
        encoding="utf-8",
    )
    frame, _ = _read_history(workspace)
    assert frame["monthly_value_try"].round(0).tolist() == [1394.0, 1800.0]


def test_the_synthetic_marker_is_detected_on_the_raw_columns():
    marked = pd.DataFrame(
        {backtest.SYNTHETIC_MARKER_COLUMN: [backtest.SYNTHETIC_MARKER_VALUE]}
    )
    assert backtest.detect_synthetic(marked) is True
    assert backtest.detect_synthetic(pd.DataFrame({"a": [1]})) is False


def test_missing_cli_arguments_say_which_ones(tmp_path):
    args = backtest.parse_args(["--history", str(tmp_path / "h.csv")])
    with pytest.raises(BacktestInputError) as error:
        backtest.build_report(args)
    message = str(error.value)
    assert "--outcomes, --mapping is required" in message
    assert "--contract" in message


def test_non_positive_parameters_are_refused_rather_than_looping(tmp_path):
    """A step of 0 would make the origin grid infinite; a k of 0 flags nobody."""
    args = backtest.parse_args(
        [
            "--history", str(tmp_path / "h.csv"),
            "--outcomes", str(tmp_path / "o.csv"),
            "--mapping", str(tmp_path / "m.json"),
            "--step-days", "0",
            "--k", "0",
        ]
    )
    with pytest.raises(BacktestInputError) as error:
        backtest.build_report(args)
    message = str(error.value)
    assert "--step-days=0" in message
    assert "--k=0" in message


# --- 7. Baseline and verdict --------------------------------------------------
def test_the_baseline_direction_is_re_derived_on_the_training_slice():
    """A rule pointed the wrong way would be a straw man, not a baseline."""
    train = pd.DataFrame({"satisfaction_survey_score": [5.0, 4.0, 2.0, 1.0]})
    labels = pd.Series([0, 0, 1, 1])  # lower score, higher risk
    scoring = pd.DataFrame({"satisfaction_survey_score": [5.0, 1.0]})
    result = backtest._baseline_scores(
        train,
        labels,
        scoring,
        "satisfaction_survey_score",
        {"medians": {"satisfaction_survey_score": 3.0}},
        n_to_flag=1,
    )
    assert result["direction"] == "lower is riskier"
    # The low-satisfaction student is the one flagged.
    assert result["flagged"].tolist() == [False, True]


def test_the_baseline_flags_exactly_as_many_students_as_the_model_did():
    train = pd.DataFrame({"tenure_months": [1.0, 2.0, 3.0, 4.0]})
    scoring = pd.DataFrame({"tenure_months": [1.0, 2.0, 3.0, 4.0]})
    result = backtest._baseline_scores(
        train, pd.Series([1, 1, 0, 0]), scoring, "tenure_months",
        {"medians": {"tenure_months": 2.5}}, n_to_flag=2,
    )
    assert int(result["flagged"].sum()) == 2


def test_a_baseline_column_that_was_not_supplied_is_reported_not_crashed():
    result = backtest._baseline_scores(
        pd.DataFrame({"a": [1.0]}), pd.Series([0]), pd.DataFrame({"a": [1.0]}),
        "not_supplied", {"medians": {}}, n_to_flag=1,
    )
    assert result["column"] is None
    assert not result["flagged"].any()


def test_the_verdict_says_so_when_the_difference_cannot_be_measured():
    verdict = backtest._verdict(
        {"value": 0.30, "ci95_low": 0.10, "ci95_high": 0.50},
        {"value": 0.25, "ci95_low": 0.05, "ci95_high": 0.45},
        {"value": 0.4},
        {"value": 0.35},
    )
    assert verdict["outcome"] == "indistinguishable"
    assert "OLCULEMIYOR" in verdict["tr"]


def test_the_verdict_says_plainly_when_the_model_loses_to_the_rule():
    verdict = backtest._verdict(
        {"value": 0.10, "ci95_low": 0.08, "ci95_high": 0.12},
        {"value": 0.30, "ci95_low": 0.26, "ci95_high": 0.34},
        {"value": 0.2},
        {"value": 0.5},
    )
    assert verdict["outcome"] == "baseline_better"
    assert "GECEMIYOR" in verdict["tr"]


def test_the_verdict_can_award_the_model_the_win():
    verdict = backtest._verdict(
        {"value": 0.40, "ci95_low": 0.35, "ci95_high": 0.45},
        {"value": 0.10, "ci95_low": 0.06, "ci95_high": 0.14},
        {"value": 0.5},
        {"value": 0.2},
    )
    assert verdict["outcome"] == "model_better"


def test_choose_baseline_column_prefers_the_audited_default(workspace):
    history, info = _read_history(workspace)
    column, reason = backtest.choose_baseline_column(
        history, info["features_used"], requested=None
    )
    # message_response_time_hours is not in this file, so the densest one is used.
    assert column in info["features_used"]
    assert "densely populated" in reason

    history["message_response_time_hours"] = 1.0
    column, reason = backtest.choose_baseline_column(
        history,
        info["features_used"] + ["message_response_time_hours"],
        requested=None,
    )
    assert column == "message_response_time_hours"
    assert "src/model/baseline.py default" in reason


def test_an_unknown_baseline_column_is_refused(workspace):
    history, info = _read_history(workspace)
    with pytest.raises(BacktestInputError) as error:
        backtest.choose_baseline_column(
            history, info["features_used"], requested="nope"
        )
    assert "--baseline-column 'nope' is not a usable column" in str(error.value)


# --- 8. Money -----------------------------------------------------------------
def test_the_money_section_never_implies_a_saved_revenue_claim():
    scored = _scored(
        [
            {"student_id": "A", "origin": "2026-03-01", "churn_date": "2026-03-20",
             "flagged": 1, "label": 1, "monthly_value_try": 1500.0},
            {"student_id": "B", "origin": "2026-03-01", "churn_date": None,
             "flagged": 1, "label": 0, "monthly_value_try": 500.0},
        ]
    )
    table = pd.DataFrame(
        {"student_id": ["A"], "flagged": [1], "monthly_value_try": [1500.0]}
    )
    money = backtest._aggregate_money(scored, table)
    assert money["flagged_monthly_value_try"] == 2000.0
    assert money["caught_churner_monthly_value_try"] == 1500.0
    assert "DEGILDIR" in money["value_at_risk_not_value_saved"]
    assert "kurtarilan" not in money  # no key implies revenue saved


def test_a_student_flagged_at_four_points_counts_once_in_the_money():
    scored = _scored(
        [
            {"student_id": "A", "origin": f"2026-0{month}-01", "churn_date": None,
             "flagged": 1, "label": 0, "monthly_value_try": 1000.0}
            for month in (1, 2, 3, 4)
        ]
    )
    money = backtest._aggregate_money(scored, pd.DataFrame())
    assert money["flagged_students"] == 1
    assert money["flagged_monthly_value_try"] == 1000.0


def test_no_value_column_means_no_money_section_rather_than_a_zero():
    scored = _scored(
        [{"student_id": "A", "origin": "2026-03-01", "churn_date": None,
          "flagged": 1, "label": 0}]
    )
    scored["monthly_value_try"] = np.nan
    money = backtest._aggregate_money(scored, pd.DataFrame())
    assert money["available"] is False
    assert "monthly_value_try" in money["reason"]


# --- 9. End to end ------------------------------------------------------------
def weekly_history(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small, plain history: 250 students, weekly rows, a real drift before churn.

    Deliberately NOT the leakage fixture: this one has an honest signal (adherence
    drifts down before a student leaves) so a whole run produces non-degenerate
    numbers, and the walk-forward machinery is exercised over several folds.
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-09-01")
    rows, outcomes = [], []
    for index in range(250):
        student_id = f"S{index:04d}"
        churns = index % 6 == 0
        churn_date = (
            start + pd.Timedelta(days=int(rng.integers(70, 330))) if churns else pd.NaT
        )
        outcomes.append(
            {
                "student_id": student_id,
                "churn_date": churn_date.date() if churns else "",
            }
        )
        for week in range(40):
            as_of = start + pd.Timedelta(days=7 * week)
            if churns and churn_date <= as_of:
                continue
            # Risk ramps up over the 60 days before leaving: a genuine, declared
            # signal, so the fold metrics are not all zeros.
            closeness = (
                float(np.clip(1.0 - (churn_date - as_of).days / 60.0, 0.0, 1.0))
                if churns
                else 0.0
            )
            rows.append(
                {
                    "student_id": student_id,
                    "as_of_date": as_of.date(),
                    "program_adherence_rate": round(
                        float(np.clip(0.85 - 0.45 * closeness + rng.normal(0, 0.10), 0, 1)),
                        3,
                    ),
                    "tenure_months": round(week / 4.3, 2),
                    "trial_exam_avg_net": round(float(45 - 10 * closeness + rng.normal(0, 7)), 2),
                    "monthly_value_try": 1500.0,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(outcomes)


def test_a_whole_backtest_runs_and_the_report_carries_its_own_caveats(tmp_path):
    history, outcomes = weekly_history()
    history_path = tmp_path / "history.csv"
    history.to_csv(history_path, index=False)
    outcomes_path = tmp_path / "outcomes.csv"
    outcomes.to_csv(outcomes_path, index=False)
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"_note": "already our names"}), encoding="utf-8")

    args = backtest.parse_args(
        [
            "--history", str(history_path),
            "--outcomes", str(outcomes_path),
            "--mapping", str(mapping_path),
            "--step-days", "60",
            "--k", "10",
            "--min-train-rows", "200",
            "--min-train-churners", "5",
            "--bootstrap-resamples", "50",
            "--out", str(tmp_path / "report.json"),
        ]
    )
    report, out_path = backtest.build_report(args)
    written = backtest.write_report(report, out_path)

    assert written.exists()
    assert report["is_synthetic_data"] is False
    assert report["walk_forward"]["usable"] >= 2
    assert report["parameters"]["flag_rule"] == "capacity"
    assert report["headline"]["coverage"]["value"] > 0
    assert report["headline"]["lead_time_days"]["n"] > 0
    assert report["headline"]["coverage_ceiling"]["available"] is True
    assert report["verdict"]["outcome"] in {
        "model_better", "indistinguishable", "baseline_better", "no_baseline"
    }
    # The caveats travel with the numbers, because the customer report is generated
    # from this file and not from whoever happened to run the script.
    joined = " ".join(report["caveats"])
    assert "mudahale etmedi" in joined
    assert "Lead time cozunurlugu 60 gundur" in joined
    # And it is JSON-serialisable with no numpy or NaN left in it.
    reloaded = json.loads(written.read_text(encoding="utf-8"))
    assert reloaded["generated_by"] == "scripts/backtest.py"
    assert reloaded["headline"]["coverage"]["value"] == pytest.approx(
        report["headline"]["coverage"]["value"]
    )


def test_every_fold_of_a_real_run_obeys_the_embargo():
    """Belt and braces: the embargo recorded per fold is re-checked arithmetically."""
    history, outcomes = weekly_history()
    history["as_of_date"] = pd.to_datetime(history["as_of_date"])
    churn_dates = pd.Series(
        pd.to_datetime(outcomes["churn_date"].replace("", None)).to_numpy(),
        index=outcomes["student_id"],
    )
    body = backtest.run_backtest(
        history,
        churn_dates,
        feature_columns=[
            "program_adherence_rate",
            "tenure_months",
            "trial_exam_avg_net",
        ],
        churn_window_days=WINDOW,
        step_days=60,
        active_window_days=14,
        warmup_days=60,
        k=10,
        flag_rule="capacity",
        baseline_column="trial_exam_avg_net",
        min_train_rows=200,
        min_train_churners=5,
        val_date_fraction=0.25,
        bootstrap_resamples=50,
        seed=1,
    )
    folds = [f for f in body["folds"] if f["status"] == "ok"]
    assert len(folds) >= 2
    for fold in folds:
        origin = pd.Timestamp(fold["origin"])
        last_train = pd.Timestamp(fold["train"]["last_as_of_date"])
        assert last_train + pd.Timedelta(days=WINDOW) <= origin
        assert fold["train"]["embargo_cutoff"] == (
            origin - pd.Timedelta(days=WINDOW)
        ).date().isoformat()
        # Every scoring row describes the origin or earlier, by construction.
        assert fold["scored"]["students_scored"] > 0
    assert body["baseline"]["column"] == "trial_exam_avg_net"
