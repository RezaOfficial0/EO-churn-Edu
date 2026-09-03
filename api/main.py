"""FastAPI app for the churn early-warning system.

The model, SHAP explainer and calibrator are loaded once at startup (see
`lifespan`) and kept on `app.state`. If the model file is missing the API still
starts: `GET /health` reports "degraded" and the scoring endpoints return 503.
"""
import logging
import time
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field, create_model

from config import (
    ALLOWED_ORIGINS,
    API_KEY,
    CALIBRATOR_PATH,
    CAT_COLS,
    DAILY_DATA_PATH,
    FEATURE_BOUNDS,
    FEATURES,
    MODEL_META_PATH,
    MODEL_PATH,
    SHAP_TOP_N_FEATURES,
    STUDENT_INFO,
)
from pipeline.daily_pipeline import run_daily_pipeline
from src.data.features import build_serving_frame
from src.data.loader import data_loader
from src.data.preprocess import cast_categoricals, daily_process
from src.data.validation import DataValidationError, require_no_nulls
from src.explainer.shap_explainer import create_explainer, explain_customers
from src.logging_setup import configure_logging
from src.model.calibrate import churn_proba, load_calibrator
from src.model.load import load_meta, load_model
from src.serialization import to_native

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]


# --- Startup / shutdown -----------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if API_KEY is None:
        logger.warning("API_KEY is not set - the API is running WITHOUT authentication")

    app.state.model = None
    app.state.explainer = None
    app.state.calibrator = None
    app.state.meta = {}
    app.state.load_error = None
    try:
        app.state.model = load_model(MODEL_PATH)
        app.state.explainer = create_explainer(app.state.model)
        app.state.calibrator = load_calibrator(CALIBRATOR_PATH)
        app.state.meta = load_meta(MODEL_META_PATH) or {}
        logger.info("model loaded from %s", MODEL_PATH)
    except Exception as e:  # noqa: BLE001 - deliberately degrade instead of crash
        app.state.load_error = str(e)
        logger.exception("model failed to load - API will report degraded")
    yield


app = FastAPI(title="EOAI Churn Early Warning API", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "%s %s -> %s (%.0f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Anything not raised as an HTTPException is a server bug: log it, return 500."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# --- Dependencies --------------------------------------------------------
def require_api_key(x_api_key: str | None = Header(default=None)):
    """Reject the request unless it carries the right X-API-Key header.

    Does nothing when `API_KEY` is unset (local development)."""
    if API_KEY is None:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def get_model(request: Request):
    model = request.app.state.model
    if model is None:
        raise HTTPException(
            status_code=503,
            detail=f"model not loaded: {request.app.state.load_error or 'unknown error'}",
        )
    return model


# --- Request schema ----------------------------------------------------
def _field_spec(feature: str):
    if feature in CAT_COLS:
        return (str, ...)
    low, high = FEATURE_BOUNDS[feature]
    return (float, Field(..., ge=low, le=high))


# POST /predict body: every column in config.FEATURES, numerics range-checked.
RawCustomerIn = create_model(
    "RawCustomerIn", **{feature: _field_spec(feature) for feature in FEATURES}
)


# --- Scoring helper ------------------------------------------------------
def _score(request: Request, X: pd.DataFrame, top_n: int):
    """Score one row. `X` must contain exactly config.FEATURES."""
    model = get_model(request)
    X = cast_categoricals(X[FEATURES])
    proba = float(churn_proba(model, X, request.app.state.calibrator)[0])
    reasons = explain_customers(request.app.state.explainer, X, top_n=top_n)[0]
    return proba, [{"feature": r["feature"], "impact": r["impact"]} for r in reasons]


# --- Endpoints -------------------------------------------------------
@app.get("/health")
def health(request: Request):
    if request.app.state.model is None:
        return {
            "status": "degraded",
            "reason": request.app.state.load_error or "model not loaded",
        }
    return {"status": "ok"}


@app.post("/predict", dependencies=[Depends(require_api_key)])
def predict_raw(payload: RawCustomerIn, request: Request):
    try:
        proba, reasons = _score(
            request, pd.DataFrame([payload.model_dump()]), SHAP_TOP_N_FEATURES
        )
    except (KeyError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=f"invalid input: {e}")
    return {"churn_probability": proba, "top_reasons": reasons}


@app.get("/predict/{student_id}", dependencies=[Depends(require_api_key)])
def predict_by_student_id(student_id: str, request: Request):
    try:
        daily = data_loader(DAILY_DATA_PATH)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    match = daily[daily[_ID_COLUMN].astype(str) == str(student_id)]
    if match.empty:
        raise HTTPException(
            status_code=404, detail=f"student_id not found in daily data: {student_id}"
        )

    imputation_values = request.app.state.meta.get("imputation_values", {})
    try:
        engineered = build_serving_frame(match.iloc[[0]], imputation_values)
        require_no_nulls(engineered, FEATURES)
        customer_info, X = daily_process(engineered)
        proba, reasons = _score(request, X, SHAP_TOP_N_FEATURES)
    except (KeyError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=f"invalid input: {e}")

    return {
        **customer_info.iloc[0].to_dict(),
        "churn_probability": proba,
        "features": to_native(X.iloc[0].to_dict()),
        "top_reasons": reasons,
    }


@app.post("/run-daily-pipeline", dependencies=[Depends(require_api_key)])
def trigger_daily_pipeline(request: Request, threshold: float | None = None):
    get_model(request)
    meta = request.app.state.meta
    chosen_threshold = threshold if threshold is not None else meta.get("chosen_threshold", 0.5)
    try:
        result = run_daily_pipeline(
            DAILY_DATA_PATH,
            model=request.app.state.model,
            explainer=request.app.state.explainer,
            calibrator=request.app.state.calibrator,
            imputation_values=meta.get("imputation_values", {}),
            threshold=chosen_threshold,
        )
    except (FileNotFoundError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "churn_risk_count": len(result),
        "students": result.to_dict(orient="records"),
    }


@app.get("/metrics", dependencies=[Depends(require_api_key)])
def latest_metrics(request: Request):
    """Return the metadata (and metrics) of the currently loaded model."""
    meta = request.app.state.meta
    if not meta:
        raise HTTPException(
            status_code=404,
            detail="no trained-model metadata found; run running_train_pipeline.py first",
        )
    return meta
