"""Row-level quarantine (B-28).

One student's half-written row used to fail the whole run, so with real customer
data nobody got a message. These tests pin the four things that makes safe:

  - the DECISION: which null costs the row, and which one the imputer handles;
  - the COUNTING: how many were skipped, and for which columns (never which student);
  - the THRESHOLD: a small share continues and is reported, a large share still fails;
  - the EMPTY input: it fails loudly instead of sending a reassuring empty message.

No network and no sleeps: scoring runs against the committed sample model, and the
message builders are pure.
"""
import pandas as pd
import pytest

from config import (
    CALIBRATOR_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    QUARANTINE_WARN_RATIO,
)
from pipeline.daily_pipeline import run_daily_pipeline, score_students
from src.data.features import MISSING_FLAG_COLUMNS, SERVING_REQUIRED_COLUMNS
from src.data.run_quality import read_report, write_report
from src.data.validation import (
    QUARANTINE_REASON_COLUMN,
    DataValidationError,
    QuarantineReport,
    check_quarantine,
    quarantine_unusable_rows,
)
from src.explainer.shap_explainer import create_explainer
from src.model.calibrate import load_calibrator
from src.model.load import load_meta, load_model
from src.notifications.message import build_html, build_message, quarantine_note


# --- The decision ----------------------------------------------------------
def test_imputable_null_keeps_the_row_unusable_null_drops_it(daily_df):
    """The line the whole issue turns on, stated as one test.

    `satisfaction_survey_score` is null-able by design: the `satisfaction_missing`
    flag records it and the imputer fills it. `days_since_last_contact` is not.
    """
    frame = daily_df.head(4).copy()
    frame.loc[frame.index[0], "satisfaction_survey_score"] = None
    frame.loc[frame.index[1], "days_since_last_contact"] = None

    usable, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)

    assert len(usable) == 3
    assert len(rejected) == 1
    assert rejected.iloc[0][QUARANTINE_REASON_COLUMN] == "days_since_last_contact"


def test_every_imputable_column_is_allowed_to_be_null(daily_df):
    """All of MISSING_FLAG_COLUMNS null at once still leaves a scorable row."""
    frame = daily_df.head(1).copy()
    for column in MISSING_FLAG_COLUMNS:
        frame[column] = None

    usable, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)

    assert len(usable) == 1
    assert rejected.empty


def test_a_row_without_an_id_is_unusable(daily_df):
    """An alert nobody can look up is not an alert, so the id is required too."""
    frame = daily_df.head(2).copy()
    frame.loc[frame.index[1], "student_id"] = None

    usable, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)

    assert len(usable) == 1
    assert rejected.iloc[0][QUARANTINE_REASON_COLUMN] == "student_id"


def test_reason_lists_every_offending_column(daily_df):
    frame = daily_df.head(1).copy()
    frame["grade"] = None
    frame["plan_type"] = None

    _, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)

    assert rejected.iloc[0][QUARANTINE_REASON_COLUMN] == "grade, plan_type"


def test_a_missing_column_is_not_this_functions_business(daily_df):
    """`validate()` has already failed the run for it; quarantining every row for a
    column the frame does not have would turn one frame-level fault into 25.000."""
    frame = daily_df.head(3).drop(columns=["grade"])

    usable, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)

    assert len(usable) == 3
    assert rejected.empty


# --- The counting ----------------------------------------------------------
def _rows(count: int, *, broken: int = 0, column: str = "grade") -> pd.DataFrame:
    """`count` synthetic rows, `broken` of them null in `column`.

    Synthetic rather than a slice of the sample: the ratio tests need a round number
    of rows (10 of 100), and the committed daily sample has 25. What is being tested
    here is arithmetic on counts, not the sample.
    """
    frame = pd.DataFrame(
        {
            "student_id": [f"s{index}" for index in range(count)],
            column: ["11. Sınıf"] * count,
        }
    )
    frame.loc[frame.index[:broken], column] = None
    return frame


def test_report_counts_rows_and_columns(daily_df):
    frame = daily_df.head(10).copy()
    frame.loc[frame.index[0], "grade"] = None
    frame.loc[frame.index[1], "grade"] = None
    frame.loc[frame.index[2], "payment_delay_days_avg"] = None

    _, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)
    report = check_quarantine(len(frame), rejected, max_ratio=0.5)

    assert (report.total, report.scored, report.skipped) == (10, 7, 3)
    assert report.ratio == pytest.approx(0.3)
    assert report.reasons == {"grade": 2, "payment_delay_days_avg": 1}


def test_no_skipped_rows_is_a_quiet_report(daily_df):
    _, rejected = quarantine_unusable_rows(daily_df.head(5), SERVING_REQUIRED_COLUMNS)
    report = check_quarantine(5, rejected)

    assert (report.skipped, report.ratio, report.reportable) == (0, 0.0, False)


def test_the_log_counts_columns_and_never_names_a_student(caplog, daily_df):
    """B-12: the operator gets the column and the count, not the (mostly minor) child."""
    frame = daily_df.head(10).copy()
    frame.loc[frame.index[0], "grade"] = None
    student_id = str(frame.iloc[0]["student_id"])

    _, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)
    with caplog.at_level("WARNING"):
        check_quarantine(len(frame), rejected, max_ratio=0.5)

    assert "grade" in caplog.text
    assert student_id not in caplog.text


# --- The threshold ---------------------------------------------------------
def test_a_small_share_continues():
    frame = _rows(100, broken=1)

    _, rejected = quarantine_unusable_rows(frame, ["grade"])
    report = check_quarantine(len(frame), rejected, max_ratio=0.10)

    assert report.skipped == 1  # no exception: 1% is below the 10% limit


def test_a_share_above_the_limit_fails_the_run():
    """Half the school missing is an outage, and a message built from the other half
    is indistinguishable from a quiet day."""
    frame = _rows(100, broken=50)

    _, rejected = quarantine_unusable_rows(frame, ["grade"])
    with pytest.raises(DataValidationError, match="too many unusable rows"):
        check_quarantine(len(frame), rejected, max_ratio=0.10)


def test_the_limit_is_exclusive_at_exactly_the_limit():
    """10 of 100 with a 10% limit passes; the 11th does not. Pinned because an
    off-by-one here is the difference between a demo that runs and one that does not."""
    at_the_limit = _rows(100, broken=10)
    _, rejected = quarantine_unusable_rows(at_the_limit, ["grade"])
    assert check_quarantine(100, rejected, max_ratio=0.10).skipped == 10

    over = _rows(100, broken=11)
    _, rejected = quarantine_unusable_rows(over, ["grade"])
    with pytest.raises(DataValidationError, match="too many unusable rows"):
        check_quarantine(100, rejected, max_ratio=0.10)


def test_every_row_unusable_fails_whatever_the_limit_says():
    frame = _rows(4, broken=4)

    _, rejected = quarantine_unusable_rows(frame, ["grade"])
    with pytest.raises(DataValidationError, match="every row was unusable"):
        check_quarantine(len(frame), rejected, max_ratio=1.0)


def test_the_client_message_carries_no_column_names(daily_df):
    """The 400 body a caller sees is counts only; the columns are in the log (B-12)."""
    frame = daily_df.head(4).copy()
    frame.loc[frame.index[:2], "payment_delay_days_avg"] = None

    _, rejected = quarantine_unusable_rows(frame, SERVING_REQUIRED_COLUMNS)
    with pytest.raises(DataValidationError) as raised:
        check_quarantine(len(frame), rejected, max_ratio=0.1)

    assert "payment_delay_days_avg" not in str(raised.value)


# --- End to end through the pipeline ---------------------------------------
def _scoring_kwargs():
    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH)
    return dict(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta["imputation_values"],
        threshold=meta["chosen_threshold"],
    )


def _daily_csv(tmp_path, frame) -> str:
    path = tmp_path / "daily_data.csv"
    frame.to_csv(path, index=False)
    return str(path)


def test_one_null_no_longer_costs_the_whole_run(tmp_path, daily_df):
    """The bug, stated: before B-28 this raised and nobody got scored."""
    frame = daily_df.copy()
    frame.loc[frame.index[0], "days_since_last_contact"] = None

    result = score_students(_daily_csv(tmp_path, frame), **_scoring_kwargs())

    assert result.quarantine.skipped == 1
    assert result.quarantine.scored == len(frame) - 1
    assert len(result.rejected) == 1
    # The skipped student is not in the list, and the rest still are.
    skipped_id = str(frame.iloc[0]["student_id"])
    assert skipped_id not in set(result.at_risk["student_id"].astype(str))


def test_an_unusable_row_is_not_scored_and_the_rest_are_unchanged(tmp_path, daily_df):
    """Quarantine must not move anyone else's probability."""
    clean = score_students(_daily_csv(tmp_path, daily_df), **_scoring_kwargs())

    poisoned = daily_df.copy()
    # Break a student the clean run did NOT flag, so the two at-risk lists can be
    # compared directly.
    at_risk_ids = set(clean.at_risk["student_id"].astype(str))
    victim = next(
        index for index in poisoned.index
        if str(poisoned.loc[index, "student_id"]) not in at_risk_ids
    )
    poisoned.loc[victim, "mentor_contact_freq_per_month"] = None

    after = score_students(_daily_csv(tmp_path, poisoned), **_scoring_kwargs())

    assert after.quarantine.skipped == 1
    pd.testing.assert_series_equal(
        clean.at_risk["churn_probability"], after.at_risk["churn_probability"]
    )


def test_a_broken_export_still_fails_the_run(tmp_path, daily_df):
    frame = daily_df.copy()
    frame.loc[frame.index[: len(frame) // 2], "track"] = None

    with pytest.raises(DataValidationError, match="too many unusable rows"):
        score_students(_daily_csv(tmp_path, frame), **_scoring_kwargs())


def test_an_entirely_empty_input_fails_loudly(tmp_path, daily_df):
    """An empty file must never produce "bugün risk altında öğrenci yok"."""
    empty = daily_df.iloc[0:0]

    with pytest.raises(DataValidationError, match="empty"):
        score_students(_daily_csv(tmp_path, empty), **_scoring_kwargs())


def test_a_run_leaves_its_quality_summary_behind(tmp_path, daily_df):
    """The pipeline and the notification step are two processes; this file is how the
    second one finds out what the first one skipped."""
    frame = daily_df.copy()
    frame.loc[frame.index[0], "grade"] = None
    quality_path = tmp_path / "state" / "last_run_quality.json"

    result = run_daily_pipeline(
        _daily_csv(tmp_path, frame),
        alerts_path=str(tmp_path / "daily_alerts.csv"),
        quality_path=str(quality_path),
        **_scoring_kwargs(),
    )

    recorded = read_report(quality_path)
    assert recorded is not None
    assert (recorded.total, recorded.skipped) == (result.quarantine.total, 1)
    # Counts only - the file is on a volume and an id must not reach it (B-12).
    assert str(frame.iloc[0]["student_id"]) not in quality_path.read_text(encoding="utf-8")


def test_scoring_alone_writes_no_quality_file(tmp_path, daily_df):
    """`GET /students` calls score_students on every page load: still pure."""
    quality_path = tmp_path / "state" / "last_run_quality.json"

    score_students(_daily_csv(tmp_path, daily_df), **_scoring_kwargs())

    assert not quality_path.exists()


# --- The run-quality hand-off ----------------------------------------------
def test_report_round_trips(tmp_path):
    path = tmp_path / "quality.json"
    report = QuarantineReport(total=100, scored=97, skipped=3, reasons={"grade": 3})

    write_report(path, report)

    assert read_report(path) == report


def test_a_missing_or_corrupt_record_costs_the_line_not_the_message(tmp_path):
    """Reading must never raise: the alert still has to go out."""
    assert read_report(tmp_path / "nope.json") is None

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_report(broken) is None


def test_an_unwritable_directory_does_not_fail_the_run(tmp_path):
    """A read-only state mount must cost the message a line, not the whole run."""
    blocker = tmp_path / "state"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")

    write_report(blocker / "quality.json", QuarantineReport(total=1, scored=1, skipped=0))


def test_a_hand_edited_ratio_cannot_override_the_counts(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text('{"total": 10, "scored": 9, "skipped": 1, "ratio": 0.99}', encoding="utf-8")

    assert read_report(path).ratio == pytest.approx(0.1)


# --- What a human is told --------------------------------------------------
def test_reportable_follows_the_warn_ratio():
    assert QuarantineReport(total=1000, scored=999, skipped=1).reportable is False
    assert QuarantineReport(total=10, scored=9, skipped=1).reportable is True
    assert QuarantineReport(total=10, scored=10, skipped=0).reportable is False
    assert QUARANTINE_WARN_RATIO == 0.01  # the default the two cases above assume


def test_the_note_appears_in_the_message_only_when_it_is_reportable():
    loud = QuarantineReport(total=10, scored=9, skipped=1, reasons={"grade": 1})
    quiet = QuarantineReport(total=1000, scored=999, skipped=1, reasons={"grade": 1})

    assert "değerlendirilemedi" in quarantine_note(loud)
    assert quarantine_note(quiet) == ""
    assert quarantine_note(None) == ""


def test_an_empty_list_with_skipped_rows_says_so():
    """The run that matters most: no alerts AND unread rows must not read as calm."""
    empty = pd.DataFrame(columns=["student_id", "churn_probability", "status"])
    report = QuarantineReport(total=10, scored=5, skipped=5, reasons={"grade": 5})

    _, text = build_message(empty, quarantine=report)
    html = build_html(empty, quarantine=report)

    assert "risk eşiğinin üzerinde öğrenci yok" in text
    assert "5 kayıt eksik bilgi" in text
    assert "5 kayıt eksik bilgi" in html


def test_the_note_names_no_column_and_no_student():
    """A mentor cannot act on "days_since_last_contact"; the operator's log can."""
    report = QuarantineReport(total=10, scored=9, skipped=1, reasons={"grade": 1})

    note = quarantine_note(report)

    assert "grade" not in note
