"""Live smoke test - needs a running server (`uvicorn api.main:app`) and the
sample daily data. This is a quick end-to-end check, not the unit-test suite;
run `pytest` for that.

    uvicorn api.main:app &
    python scripts/test_api.py

Every endpoint the API still exposes is read-only, so this touches nothing and can
be re-run as often as you like. The day's run is `python -m pipeline.daily_pipeline`
and is deliberately not reachable over HTTP (B-08). Skipped checks are reported as
SKIP and do not affect the exit code, which is 0 only when nothing FAILed.

Set API_KEY in the environment: the server does not start without one unless it was
started with EOAI_ALLOW_NO_AUTH=1.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import (
    CAT_COLS,
    DAILY_DATA_PATH,
    FEATURE_BOUNDS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
)
from src.data.features import add_monthly_value

BASE_URL = "http://127.0.0.1:8000"
API_KEY = os.environ.get("API_KEY")
results = []


def call(method, path, payload=None):
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    results.append((name, status))
    print(f"[{status}] {name} {detail}")


def skip(name, reason=""):
    """A check that was deliberately not run. Never counts as a failure."""
    results.append((name, "SKIP"))
    print(f"[SKIP] {name} {reason}")


def valid_predict_body(daily):
    """POST /predict takes MODEL features, so derived ones have to be computed.

    Counters and flags go as `int`: the endpoint refuses a float there (B-10), which
    is the point - "2.7 support tickets" is a broken client, not a measurement.
    """
    row = add_monthly_value(daily).iloc[0]
    body = {}
    for feature in FEATURES:
        if feature in CAT_COLS:
            body[feature] = str(row[feature])
            continue
        if feature in row and pd.notna(row[feature]):
            value = float(row[feature])
        else:
            value = float(FEATURE_BOUNDS[feature][0])
        body[feature] = int(value) if feature in INTEGER_FEATURES + FLAG_FEATURES else value
    return body


def student_record_problems(student):
    """Every contract violation in one scored-student record, as a list of strings.

    This is the shape the dashboard reads field by field, so a renamed or
    dropped feature has to fail here - otherwise the frontend just starts
    rendering "yok" and nobody notices.
    """
    problems = []

    student_id = student.get("student_id")
    if not isinstance(student_id, str) or not student_id:
        problems.append(f"student_id is not a non-empty string: {student_id!r}")

    probability = student.get("churn_probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        problems.append(f"churn_probability is not a number: {probability!r}")
    elif not 0.0 <= float(probability) <= 1.0:
        problems.append(f"churn_probability outside [0, 1]: {probability!r}")

    reasons = student.get("top_reasons_detail")
    if not isinstance(reasons, list):
        problems.append(f"top_reasons_detail is not a list: {reasons!r}")
    else:
        for reason in reasons:
            feature_ok = isinstance(reason, dict) and isinstance(reason.get("feature"), str)
            impact_ok = isinstance(reason, dict) and isinstance(reason.get("impact"), (int, float))
            if not (feature_ok and impact_ok):
                problems.append(f"malformed entry in top_reasons_detail: {reason!r}")
                break

    features = student.get("features")
    if not isinstance(features, dict):
        problems.append(f"features is not an object: {features!r}")
    else:
        missing = [name for name in FEATURES if name not in features]
        if missing:
            problems.append(f"features is missing {len(missing)} key(s): {missing[:5]}")

    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Live smoke test for the churn API. Read-only: the API has no "
                    "write endpoint any more.",
    )
    parser.parse_args(argv)

    daily = pd.read_csv(DAILY_DATA_PATH)
    student_id = str(daily.iloc[0]["student_id"])

    status, body = call("GET", "/health")
    check("GET /health", status == 200 and body.get("status") == "ok", f"-> {status}")

    status, metrics = call("GET", "/metrics")
    check("GET /metrics", status == 200 and metrics.get("is_synthetic_data") is True, f"-> {status}")
    chosen_threshold = metrics.get("chosen_threshold")

    status, body = call("POST", "/predict", valid_predict_body(daily))
    raw_proba = body.get("churn_probability")
    check("POST /predict", status == 200 and raw_proba is not None, f"-> {status} proba={raw_proba}")

    status, body = call("GET", f"/predict/{student_id}")
    id_proba = body.get("churn_probability")
    check("GET /predict/{student_id}", status == 200 and id_proba is not None,
          f"-> {status} proba={id_proba}")

    status, body = call("GET", "/predict/does-not-exist-999")
    check("GET /predict/{student_id} 404 case", status == 404, f"-> {status}")

    # GET /students is what a dashboard calls on every page load, so its
    # contract is checked here rather than assumed.
    status, listed = call("GET", "/students")
    listed_students = listed.get("students")
    check(
        "GET /students",
        status == 200
        and isinstance(listed_students, list)
        and isinstance(listed.get("count"), int)
        and listed.get("threshold") is not None
        and len(listed_students) == listed["count"],
        f"-> {status} count={listed.get('count')} threshold={listed.get('threshold')}",
    )

    # The dashboard takes the threshold from /metrics and filters client-side.
    # If the two ever disagree, the screen and the daily alert show different
    # students and nothing complains.
    check(
        "GET /students threshold matches /metrics",
        chosen_threshold is not None and listed.get("threshold") == chosen_threshold,
        f"-> students={listed.get('threshold')} metrics={chosen_threshold}",
    )

    status, everyone = call("GET", "/students?threshold=0")
    scored = everyone.get("count")
    check(
        "GET /students?threshold=0 scores everyone",
        status == 200 and scored == len(daily),
        f"-> {status} count={scored} expected={len(daily)}",
    )

    sample = (everyone.get("students") or [{}])[0]
    problems = student_record_problems(sample)
    check(
        "GET /students record shape",
        not problems,
        f"-> {sample.get('student_id')}" if not problems else f"-> {problems}",
    )

    # The write endpoint was removed (B-08). A server that still answers it is
    # running older code than this repo, which is worth failing over.
    status, _ = call("POST", "/run-daily-pipeline")
    check("POST /run-daily-pipeline is gone", status in (404, 405), f"-> {status}")

    print()
    failed = [name for name, status in results if status == "FAIL"]
    passed = [name for name, status in results if status == "OK"]
    skipped = [name for name, status in results if status == "SKIP"]
    if failed:
        print(f"{len(failed)} check(s) FAILED: {failed}")
        return 1
    summary = f"all {len(passed)} checks passed"
    if skipped:
        summary += f", {len(skipped)} skipped"
    print(summary + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
