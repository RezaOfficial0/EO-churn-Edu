import os
import pandas as pd
from sqlalchemy import create_engine
from config import DATABASE_URL, DAILY_DATA_PATH, DATA_SOURCE


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    return _engine

# Import csv as DataFrame
def data_loader_csv(PATH):
    if not os.path.exists(PATH):
        raise FileNotFoundError(f"Data file not found: {PATH}")
    return pd.read_csv(PATH)


def load_daily_students_db():
    df = pd.read_sql(
        "SELECT student_id, enrollment_date, features FROM daily_students",
        _get_engine(),
    )
    features_df = pd.json_normalize(df["features"])
    return pd.concat([df[["student_id", "enrollment_date"]], features_df], axis=1)


def load_daily_students():
    if DATA_SOURCE == "db":
        return load_daily_students_db()
    return data_loader_csv(DAILY_DATA_PATH)


def get_latest_statuses_db(student_ids):
    engine = _get_engine()
    query = """
        SELECT DISTINCT ON (student_id) student_id, status
        FROM alerts
        WHERE student_id = ANY(%(ids)s)
        ORDER BY student_id, run_at DESC
    """
    df = pd.read_sql(query, engine, params={"ids": list(student_ids)})
    return dict(zip(df["student_id"], df["status"]))


def append_to_alert_log_db(at_risk_df):
    at_risk_df[["student_id", "churn_probability", "status", "top_reasons"]].to_sql(
        "alerts", _get_engine(), if_exists="append", index=False
    )