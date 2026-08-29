import json
import sys
import urllib.request
import urllib.error

import pandas as pd

BASE_URL = "http://127.0.0.1:8000"
results = []


def call(method, path, payload=None):
    url = BASE_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, condition, detail=""):
    status = "OK" if condition else "FAIL"
    results.append((name, status))
    print(f"[{status}] {name} {detail}")


df = pd.read_csv("data/daily_data.csv")
real_student_id = str(df.iloc[0]["student_id"])
raw_payload = df.drop(columns=["student_id", "enrollment_date"]).iloc[0].to_dict()

status, body = call("GET", "/health")
check("GET /health", status == 200 and body.get("status") == "ok", f"-> {status}")

status, body = call("GET", "/metrics")
check("GET /metrics", status == 200 and "roc_auc" in body, f"-> {status}")

status, body = call("POST", "/predict", raw_payload)
predict_raw_proba = body.get("churn_probability")
check("POST /predict", status == 200 and "churn_probability" in body, f"-> {status} proba={predict_raw_proba}")

status, body = call("GET", f"/predict/{real_student_id}")
predict_id_proba = body.get("churn_probability")
check("GET /predict/{student_id}", status == 200 and "churn_probability" in body, f"-> {status} proba={predict_id_proba}")

check(
    "predict raw == predict/{id} tutarliligi",
    predict_raw_proba is not None and predict_id_proba is not None
    and abs(predict_raw_proba - predict_id_proba) < 1e-6,
    f"-> {predict_raw_proba} vs {predict_id_proba}",
)

status, body = call("GET", "/predict/does-not-exist-999")
check("GET /predict/{student_id} 404 case", status == 404, f"-> {status}")

status, body = call("POST", "/run-daily-pipeline")
check("POST /run-daily-pipeline", status == 200 and "churn_risk_count" in body, f"-> {status} risky={body.get('churn_risk_count')}")

print()
failed = [name for name, s in results if s == "FAIL"]
if failed:
    print(f"{len(failed)} test FAILED: {failed}")
    sys.exit(1)
print(f"Tum {len(results)} test gecti.")
