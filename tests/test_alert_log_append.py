"""B-08: concurrent appends to the CSV alert log must not interleave.

`POST /run-daily-pipeline` is gone, so the day's run only happens from the
scheduler - but a retry can still overlap the attempt it replaces, and the old
`to_csv(mode="a")` wrote one frame in several unsynchronised write() calls.
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from src.data.loader import append_to_alert_log_csv

_ROWS_PER_RUN = 40
_RUNS = 8


def _run(alerts_path: str, run_index: int) -> None:
    frame = pd.DataFrame(
        {
            "student_id": [f"STU{run_index:02d}{i:04d}" for i in range(_ROWS_PER_RUN)],
            "churn_probability": [0.5] * _ROWS_PER_RUN,
            "status": ["new"] * _ROWS_PER_RUN,
            # Long enough that an unsynchronised write would have to split it.
            "top_reasons": ["days_since_last_contact (+0.59)" * 20] * _ROWS_PER_RUN,
        }
    )
    append_to_alert_log_csv(frame, alerts_path)


def test_concurrent_appends_produce_one_well_formed_log(tmp_path):
    alerts_path = str(tmp_path / "daily_alerts.csv")

    with ThreadPoolExecutor(max_workers=_RUNS) as pool:
        list(pool.map(lambda i: _run(alerts_path, i), range(_RUNS)))

    log = pd.read_csv(alerts_path)
    assert len(log) == _RUNS * _ROWS_PER_RUN
    assert list(log.columns) == [
        "run_at",
        "run_date",
        "student_id",
        "churn_probability",
        "status",
        "top_reasons",
    ]
    # A second header row inside the file would show up as a literal "student_id"
    # value; a spliced line as a null or a malformed probability.
    assert not (log["student_id"] == "student_id").any()
    assert log["churn_probability"].astype(float).eq(0.5).all()
    assert log.notna().all().all()
    assert log["student_id"].nunique() == _RUNS * _ROWS_PER_RUN


def test_header_is_written_once_for_an_empty_existing_file(tmp_path):
    """The header decision is taken from the file size under the lock, not from
    `path.exists()` - a file created but not yet written must still get one."""
    alerts_path = tmp_path / "daily_alerts.csv"
    alerts_path.touch()

    _run(str(alerts_path), 0)

    assert pd.read_csv(alerts_path).iloc[0]["student_id"] == "STU000000"
