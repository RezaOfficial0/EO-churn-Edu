"""The CSV/DB dispatchers: the CSV side must behave exactly as before, and the
dispatcher must honour `DATA_SOURCE` without importing a database driver."""
import pandas as pd
import pytest

from config import DAILY_DATA_PATH
from src.data import loader


def test_load_daily_students_csv_matches_read_csv():
    assert loader.load_daily_students(DAILY_DATA_PATH).equals(pd.read_csv(DAILY_DATA_PATH))


def test_data_loader_still_exists_for_training():
    """training_pipeline.py imports this name - it must not be renamed away."""
    assert loader.data_loader(DAILY_DATA_PATH).shape[0] > 0
    assert loader.data_loader_csv is loader.data_loader


def test_unknown_data_source_is_rejected():
    with pytest.raises(ValueError):
        loader.load_daily_students(DAILY_DATA_PATH, source="mysql")


def test_previous_at_risk_ids_is_empty_without_a_log(tmp_path):
    assert loader.previous_at_risk_ids(tmp_path / "nope.csv") == set()


def test_previous_at_risk_ids_reads_only_the_last_run(tmp_path):
    alerts_path = tmp_path / "alerts.csv"
    pd.DataFrame(
        {
            "run_at": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
            "run_date": ["2026-01-01", "2026-01-02"],
            "student_id": ["OLD", "RECENT"],
            "churn_probability": [0.9, 0.9],
            "status": ["new", "new"],
            "top_reasons": ["a", "b"],
        }
    ).to_csv(alerts_path, index=False)

    assert loader.previous_at_risk_ids(alerts_path) == {"RECENT"}


def test_previous_run_probabilities_is_empty_without_a_log(tmp_path):
    assert loader.previous_run_probabilities(tmp_path / "nope.csv") == {}


def test_previous_run_probabilities_is_empty_with_only_one_run(tmp_path):
    """Nothing to compare against - the message then shows no arrow."""
    alerts_path = tmp_path / "alerts.csv"
    pd.DataFrame(
        {
            "run_at": ["2026-01-02T00:00:00Z"],
            "run_date": ["2026-01-02"],
            "student_id": ["ONLY"],
            "churn_probability": [0.5],
            "status": ["new"],
            "top_reasons": ["a"],
        }
    ).to_csv(alerts_path, index=False)

    assert loader.previous_run_probabilities(alerts_path) == {}


def test_previous_run_probabilities_reads_the_second_newest_run(tmp_path):
    alerts_path = tmp_path / "alerts.csv"
    pd.DataFrame(
        {
            "run_at": [
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-02T00:00:00Z",
                "2026-01-03T00:00:00Z",
            ],
            "run_date": ["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"],
            "student_id": ["OLDEST", "A", "B", "A"],
            "churn_probability": [0.1, 0.42, 0.33, 0.56],
            "status": ["new", "new", "new", "still_at_risk"],
            "top_reasons": ["a", "b", "c", "d"],
        }
    ).to_csv(alerts_path, index=False)

    # The run before the latest one - not the latest, and not every run ever.
    assert loader.previous_run_probabilities(alerts_path) == {"A": 0.42, "B": 0.33}


def test_append_to_alert_log_csv_keeps_the_column_layout(tmp_path):
    alerts_path = tmp_path / "alerts.csv"
    at_risk = pd.DataFrame(
        {
            "student_id": ["STU1"],
            "churn_probability": [0.77],
            "status": ["new"],
            "top_reasons": ["days_since_last_contact (+0.5)"],
        }
    )

    loader.append_to_alert_log(at_risk, alerts_path)
    written = pd.read_csv(alerts_path)

    assert list(written.columns) == [
        "run_at",
        "run_date",
        "student_id",
        "churn_probability",
        "status",
        "top_reasons",
    ]
    assert len(written) == 1
