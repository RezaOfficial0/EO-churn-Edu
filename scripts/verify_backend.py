"""Run the whole backend end to end and report, step by step, what works.

This is the "is the backend actually working?" command. It walks the real chain -
model -> data source -> scoring -> alert log -> API - using the same code paths the
API uses, and prints a pass/fail line for each step. Exit code is 0 only if
everything passed, so it also works as a smoke test in a script.

    python scripts/verify_backend.py                  # whatever DATA_SOURCE says, read-only
    python scripts/verify_backend.py --source db      # force the database backend
    python scripts/verify_backend.py --source csv     # force the file backend
    python scripts/verify_backend.py --write          # also record a real daily run
    python scripts/verify_backend.py --train          # retrain the model first (slow)

Read-only by default: without `--write` nothing is inserted into `alerts` (or
appended to daily_alerts.csv), so it is safe to run against a live setup. The
scoring step actively verifies that nothing was written.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The API step below runs the app in-process with TestClient: no port is ever bound,
# so there is nothing for an API key to protect. Declaring that here - before config
# is imported, which is when the environment is read - keeps a read-only backend check
# from requiring the operator to invent an API_KEY first (B-07). An API_KEY that IS
# set still takes effect; these two only matter when there is none.
os.environ.setdefault("EOAI_ALLOW_NO_AUTH", "1")
os.environ.setdefault("API_BIND_HOST", "127.0.0.1")

_FAILURES: list[str] = []
_STEP = 0
_TOTAL = 8


def step(title: str) -> None:
    global _STEP
    _STEP += 1
    print(f"\n[{_STEP}/{_TOTAL}] {title}")


def ok(message: str) -> None:
    print(f"      OK    {message}")


def info(message: str) -> None:
    print(f"            {message}")


def skip(message: str) -> None:
    print(f"      skip  {message}")


def fail(message: str) -> None:
    _FAILURES.append(message)
    print(f"      FAIL  {message}")


def mask(url: str | None) -> str:
    """Hide the password so this output can be pasted into a chat or an issue."""
    if not url:
        return "(not set)"
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    credentials, host = rest.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", choices=["csv", "db"], help="override DATA_SOURCE for this run")
    parser.add_argument("--write", action="store_true", help="perform a real daily run (writes)")
    parser.add_argument("--train", action="store_true", help="retrain the model first")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # DATA_SOURCE has to be in the environment BEFORE config is imported, since
    # config reads it once at import time. Hence the local imports below.
    if args.source:
        os.environ["DATA_SOURCE"] = args.source

    # --- 1. Environment ------------------------------------------------------
    step("Environment")
    import config
    from src.logging_setup import configure_logging

    print()  # keep the banner clear of the log lines that follow
    ok(f"python {sys.version.split()[0]}")
    ok(f"DATA_SOURCE = {config.DATA_SOURCE}")
    if config.DATA_SOURCE == "db":
        if config.DATABASE_URL:
            ok(f"DATABASE_URL = {mask(config.DATABASE_URL)}")
        else:
            fail("DATA_SOURCE=db but DATABASE_URL is not set (see .env.example)")
            return report()
    else:
        info(f"daily data : {config.DAILY_DATA_PATH}")
        info(f"alert log  : {config.DAILY_ALERTS_PATH}")
    ok(f"API auth {'on' if config.API_KEY else 'OFF (no API_KEY set)'}")

    # --- 2. Model ------------------------------------------------------------
    step("Model")
    if args.train:
        from pipeline.training_pipeline import run_training_pipeline

        configure_logging()
        started = time.perf_counter()
        run_training_pipeline(config.RAW_DATA_PATH, config.MODEL_PATH)
        ok(f"retrained in {time.perf_counter() - started:.0f}s")

    from src.explainer.shap_explainer import create_explainer
    from src.model.calibrate import load_calibrator
    from src.model.load import load_meta, load_model

    try:
        model = load_model(config.MODEL_PATH)
        explainer = create_explainer(model)
        calibrator = load_calibrator(config.CALIBRATOR_PATH)
        meta = load_meta(config.MODEL_META_PATH) or {}
    except Exception as e:  # noqa: BLE001 - report it, do not crash the check
        fail(f"could not load the model: {e}")
        return report()

    threshold = meta.get("chosen_threshold", 0.5)
    ok(f"model loaded from {Path(config.MODEL_PATH).name}")
    ok(f"calibrator {'loaded' if calibrator is not None else 'MISSING - raw probabilities'}")
    ok(f"threshold {threshold}, trained at {meta.get('trained_at', 'unknown')}")
    if meta.get("is_synthetic_data"):
        info("note: this model was trained on synthetic data")

    # --- 3. Data source ------------------------------------------------------
    step(f"Data source ({config.DATA_SOURCE})")
    from src.data.loader import load_daily_students

    try:
        students = load_daily_students()
    except Exception as e:  # noqa: BLE001
        fail(f"could not read today's students: {e}")
        if config.DATA_SOURCE == "db":
            info("if the tables are missing, create them with: python scripts/init_db.py")
        return report()
    if students.empty:
        # Catch this before validation, which would otherwise report 22 "missing
        # columns" for what is really just an empty table.
        if config.DATA_SOURCE == "db":
            fail("the daily_students table is empty - load it with: "
                 "python scripts/load_daily_students.py")
        else:
            fail(f"{config.DAILY_DATA_PATH} has no rows")
        return report()
    ok(f"{len(students)} student(s), {len(students.columns)} column(s)")

    from src.data.features import RAW_FEATURE_COLUMNS
    from src.data.validation import DataValidationError, validate

    try:
        validate(students, config.STUDENT_INFO + RAW_FEATURE_COLUMNS, max_null_ratio=1.0)
        ok("passes validation (columns, ids, duplicates)")
    except DataValidationError as e:
        fail(f"validation: {e}")
        return report()

    # --- 4. CSV/DB parity ----------------------------------------------------
    step("CSV/DB parity")
    if config.DATA_SOURCE != "db":
        skip("only meaningful in db mode")
    elif not Path(config.DAILY_DATA_PATH).exists():
        skip(f"no CSV to compare against at {config.DAILY_DATA_PATH}")
    else:
        import pandas as pd

        from src.data.loader import load_daily_students_csv

        from_csv = load_daily_students_csv()
        if set(students.columns) != set(from_csv.columns):
            only_db = sorted(set(students.columns) - set(from_csv.columns))
            only_csv = sorted(set(from_csv.columns) - set(students.columns))
            fail(f"different columns - only in db: {only_db}, only in csv: {only_csv}")
        elif set(students["student_id"]) != set(from_csv["student_id"]):
            missing = len(set(from_csv["student_id"]) - set(students["student_id"]))
            extra = len(set(students["student_id"]) - set(from_csv["student_id"]))
            info(f"different students: {missing} in the CSV are not in the DB, {extra} the other way")
            info("that is expected if the DB has moved on - run scripts/load_daily_students.py to resync")
            ok("same schema (student sets differ, not compared row by row)")
        else:
            # JSONB does not preserve key order, so align columns before comparing.
            left = students[from_csv.columns].sort_values("student_id").reset_index(drop=True)
            right = from_csv.sort_values("student_id").reset_index(drop=True)
            try:
                pd.testing.assert_frame_equal(left, right)
                ok("the database returns exactly what the CSV does (values and dtypes)")
            except AssertionError as e:
                fail(f"db and csv disagree - the two backends would score differently:\n{e}")

    # --- 5. Scoring (read-only) ---------------------------------------------
    step("Scoring (read-only)")
    from pipeline.daily_pipeline import score_students

    before = alert_count(config)
    started = time.perf_counter()
    try:
        scored = score_students(
            model=model,
            explainer=explainer,
            calibrator=calibrator,
            imputation_values=meta.get("imputation_values", {}),
            threshold=threshold,
        )
    except Exception as e:  # noqa: BLE001
        fail(f"scoring failed: {e}")
        return report()
    elapsed_ms = (time.perf_counter() - started) * 1000

    ok(f"{len(scored)} student(s) at or above {threshold} of {len(students)} ({elapsed_ms:.0f} ms)")
    if len(scored):
        top = scored.iloc[0]
        info(f"riskiest: {top['student_id']}  p={top['churn_probability']:.4f}")
        info(f"          {top['top_reasons']}")
    if alert_count(config) == before:
        ok("nothing was written - GET /students is safe to call repeatedly")
    else:
        fail("scoring wrote to the alert log; it must be side-effect free")

    # --- 6. Daily run (writes) ----------------------------------------------
    step("Daily run (writes)")
    if not args.write:
        skip("read-only run - pass --write to record a real run")
    else:
        from pipeline.daily_pipeline import log_alerts

        before = alert_count(config)
        recorded = log_alerts(scored)
        after = alert_count(config)
        written = after - before if before is not None else None
        if written == len(recorded):
            ok(f"{written} row(s) recorded in the alert log")
        else:
            fail(f"expected {len(recorded)} new alert row(s), the log grew by {written}")
        counts = recorded["status"].value_counts().to_dict() if len(recorded) else {}
        ok(f"status: {counts or 'no at-risk students this run'}")

    # --- 7. Notifications ----------------------------------------------------
    step("Notifications")
    from config import NOTIFY_CHANNELS
    from src.notifications.message import build_message
    from src.notifications.notify import send_notifications

    try:
        # Never sends: dry_run short-circuits before any channel is touched.
        subject, body = build_message(scored.head(0), still_at_risk=scored, students=students)
        ok(f"message builds ({len(body)} chars): {subject}")
        results = send_notifications(
            scored.head(0), still_at_risk=scored, students=students, dry_run=True
        )
    except Exception as e:  # noqa: BLE001
        fail(f"could not build the alert message: {e}")
        results = {}

    if NOTIFY_CHANNELS:
        ok(f"channels configured: {', '.join(NOTIFY_CHANNELS)}")
        info("this check never sends - use: python scripts/send_daily_alerts.py --dry-run")
    else:
        skip("NOTIFY_CHANNELS is empty - the daily alert is printed only")

    # --- 8. API --------------------------------------------------------------
    step("API")
    try:
        from fastapi.testclient import TestClient

        from api.main import app
    except Exception as e:  # noqa: BLE001
        fail(f"could not import the API: {e}")
        return report()

    headers = {"X-API-Key": config.API_KEY} if config.API_KEY else {}
    with TestClient(app, headers=headers, raise_server_exceptions=False) as client:
        health = client.get("/health")
        if health.status_code == 200 and health.json().get("status") == "ok":
            ok("GET /health -> ok")
        else:
            fail(f"GET /health -> {health.status_code} {health.text[:120]}")

        before = alert_count(config)
        response = client.get("/students")
        if response.status_code == 200:
            body = response.json()
            ok(f"GET /students -> {body['count']} student(s) at threshold {body['threshold']}")
            if alert_count(config) != before:
                fail("GET /students wrote to the alert log; it must be read-only")
            if body["count"] and "status" in body["students"][0]:
                fail("GET /students returned a `status` field; it does not record a run")
        else:
            fail(f"GET /students -> {response.status_code} {response.text[:120]}")

        response = client.get("/metrics")
        if response.status_code == 200:
            roc = response.json().get("metrics", {}).get("roc_auc")
            ok(f"GET /metrics -> ok{f' (roc_auc {roc:.3f})' if isinstance(roc, float) else ''}")
        else:
            fail(f"GET /metrics -> {response.status_code} {response.text[:120]}")

        if len(scored):
            student_id = scored.iloc[0]["student_id"]
            response = client.get(f"/predict/{student_id}")
            if response.status_code == 200:
                ok(f"GET /predict/{student_id} -> p={response.json()['churn_probability']:.4f}")
            else:
                fail(f"GET /predict/{student_id} -> {response.status_code} {response.text[:120]}")

    return report()


def alert_count(config):
    """Rows currently in the alert log, whichever backend is active (None if absent)."""
    if config.DATA_SOURCE == "db":
        from src.data.loader import _get_engine, _sql

        with _get_engine().connect() as connection:
            return int(connection.execute(_sql("SELECT count(*) FROM alerts")).scalar())
    path = Path(config.DAILY_ALERTS_PATH)
    if not path.exists():
        return 0
    return sum(1 for _ in path.open()) - 1  # minus the header


def report() -> int:
    print()
    if _FAILURES:
        print(f"FAILED - {len(_FAILURES)} problem(s):")
        for failure in _FAILURES:
            print(f"  - {failure.splitlines()[0]}")
        return 1
    print("All checks passed. The backend works end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
