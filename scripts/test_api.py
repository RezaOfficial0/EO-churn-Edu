"""Live smoke test - needs a running server (`uvicorn api.main:app`) and the
sample CSVs. This is a quick end-to-end check, not the unit-test suite; run
`pytest` for that.

    uvicorn api.main:app &
    python scripts/test_api.py

Set API_KEY in the environment if the server was started with one.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from config import CAT_COLS, DAILY_DATA_PATH, FEATURE_BOUNDS, FEATURES

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


def valid_predict_body():
    row = pd.read_csv(DAILY_DATA_PATH).iloc[0]
    body = {}
    for feature in FEATURES:
        if feature in CAT_COLS:
            body[feature] = str(row[feature])
        elif feature in row and pd.notna(row[feature]):
            body[feature] = float(row[feature])
        else:
            body[feature] = float(FEATURE_BOUNDS[feature][0])
    return body


student_id = str(pd.read_csv(DAILY_DATA_PATH).iloc[0]["student_id"])

status, body = call("GET", "/health")
check("GET /health", status == 200 and body.get("status") == "ok", f"-> {status}")

status, body = call("GET", "/metrics")
check("GET /metrics", status == 200 and body.get("is_synthetic_data") is True, f"-> {status}")

status, body = call("POST", "/predict", valid_predict_body())
raw_proba = body.get("churn_probability")
check("POST /predict", status == 200 and raw_proba is not None, f"-> {status} proba={raw_proba}")

status, body = call("GET", f"/predict/{student_id}")
id_proba = body.get("churn_probability")
check("GET /predict/{student_id}", status == 200 and id_proba is not None, f"-> {status} proba={id_proba}")

status, body = call("GET", "/predict/does-not-exist-999")
check("GET /predict/{student_id} 404 case", status == 404, f"-> {status}")

status, _ = call("POST", "/run-daily-pipeline")
check("POST /run-daily-pipeline", status == 200, f"-> {status}")

print()
failed = [name for name, s in results if s == "FAIL"]
if failed:
    print(f"{len(failed)} test(s) FAILED: {failed}")
    sys.exit(1)
print(f"all {len(results)} checks passed.")
