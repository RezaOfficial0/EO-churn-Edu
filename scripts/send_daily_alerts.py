"""Print (and optionally POST) the students newly flagged at risk in the latest run.

Run it after the daily pipeline:

    python -m pipeline.daily_pipeline
    python scripts/send_daily_alerts.py

If `ALERT_WEBHOOK_URL` is set (see .env.example) the same summary is POSTed there
as JSON `{"text": "..."}` (the shape Slack incoming webhooks expect).
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import ALERT_WEBHOOK_URL, DAILY_ALERTS_PATH, STUDENT_INFO

_ID_COLUMN = STUDENT_INFO[0]


def latest_new_alerts() -> pd.DataFrame:
    """The `status == "new"` rows from the most recent run in the alert log."""
    path = Path(DAILY_ALERTS_PATH)
    if not path.exists():
        return pd.DataFrame()
    log = pd.read_csv(path)
    if log.empty:
        return log
    last_run = log["run_date"].max()
    today = log[log["run_date"] == last_run]
    return today[today["status"] == "new"]


def format_alerts(alerts: pd.DataFrame) -> str:
    if alerts.empty:
        return "No new at-risk students today."
    lines = [f"{len(alerts)} new at-risk student(s):"]
    for _, row in alerts.iterrows():
        lines.append(
            f"  - {row[_ID_COLUMN]}  p(churn)={row['churn_probability']:.2f}  {row['top_reasons']}"
        )
    return "\n".join(lines)


def post_to_webhook(message: str, url: str) -> None:
    body = json.dumps({"text": message}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def main() -> None:
    message = format_alerts(latest_new_alerts())
    print(message)
    if ALERT_WEBHOOK_URL:
        post_to_webhook(message, ALERT_WEBHOOK_URL)
        print(f"(also posted to {ALERT_WEBHOOK_URL})")


if __name__ == "__main__":
    main()
