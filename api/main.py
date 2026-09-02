import json
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import create_model

from config import (
    STUDENT_INFO,
    FEATURES,
    CAT_COLS,
    DAILY_DATA_PATH,
    MODEL_PATH,
    SHAP_TOP_N_FEATURES,
)
from src.logging_config import setup_logging
from src.model.load import load_model
from src.data.loader import data_loader
from src.data.Validation import validate, DataValidationError
from src.data.preprocess import daily_process
from src.explainer.shap_explainer import create_explainer, explain_customers
from pipeline.daily_pipeline import run_daily_pipeline

setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(title="EOAI Churn Early Warning API", version="1.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


model = load_model(MODEL_PATH)
explainer = create_explainer(model)


_raw_fields = {f: (str, ...) if f in CAT_COLS else (float, ...) for f in FEATURES}
RawCustomerIn = create_model("RawCustomerIn", **_raw_fields)


def _score_row(row_df: pd.DataFrame, top_n: int):
    row_df = row_df[FEATURES].copy()
    for c in CAT_COLS:
        row_df[c] = row_df[c].astype(str)
    proba = float(model.predict_proba(row_df)[:, 1][0])
    reasons = explain_customers(explainer, row_df, top_n=top_n)[0]
    return proba, reasons


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
def predict_raw(payload: RawCustomerIn):
    try:
        row_df = pd.DataFrame([payload.model_dump()])
        proba, reasons = _score_row(row_df, SHAP_TOP_N_FEATURES)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"prediction failed: {e}")

    return {
        "churn_probability": proba,
        "top_reasons": [
            {"feature": r["feature"], "impact": r["impact"]} for r in reasons
        ],
    }


@app.get("/predict/{student_id}")
def predict_by_student_id(student_id: str):
    try:
        data = data_loader(DAILY_DATA_PATH)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))

    match = data[data["student_id"].astype(str) == str(student_id)]
    if match.empty:
        raise HTTPException(status_code=404, detail=f"student_id not found in daily data: {student_id}")

    customer_info, x_row = daily_process(match.iloc[[0]], STUDENT_INFO)
    try:
        proba, reasons = _score_row(x_row, SHAP_TOP_N_FEATURES)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"prediction failed: {e}")

    return {
        **customer_info.iloc[0].to_dict(),
        "churn_probability": proba,
        "top_reasons": [
            {"feature": r["feature"], "impact": r["impact"]} for r in reasons
        ],
    }


@app.post("/run-daily-pipeline")
def trigger_daily_pipeline(threshold: float = 0.5):
    try:
        result = run_daily_pipeline(DAILY_DATA_PATH, MODEL_PATH, threshold=threshold)
    except (FileNotFoundError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("daily pipeline failed")
        raise HTTPException(status_code=500, detail=f"daily pipeline failed: {e}")

    return {
        "churn_risk_count": len(result),
        "students": result.to_dict(orient="records"),
    }


@app.get("/metrics")
def latest_metrics(metrics_dir: str = "metrics"):
    metrics_path = Path(metrics_dir)
    if not metrics_path.exists():
        raise HTTPException(status_code=404, detail=f"metrics dir not found: {metrics_dir}")

    files = sorted(metrics_path.glob("train_metrics_*.json"))
    if not files:
        raise HTTPException(status_code=404, detail="no metrics file found yet, run training pipeline first")

    latest_file = files[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"could not read metrics file: {e}")

    return {"file": latest_file.name, **metrics}
