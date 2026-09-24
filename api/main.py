"""FastAPI app for the churn early-warning system.

The model, SHAP explainer, calibrator and model metadata are loaded once at startup
(see `lifespan`) and published to `app.state` only when all four succeeded. If any of
them fails the API still starts, but honestly: `GET /health` reports "degraded" with
the component that is missing, and every scoring endpoint returns 503. A calibrator
that could not be loaded is one of those failures, not a silent fallback to raw
CatBoost scores.

Every endpoint here is read-only. The day's run - the one operation that writes to
the alert log and therefore decides what `new` / `still_at_risk` means tomorrow -
belongs to the scheduler (`python -m pipeline.daily_pipeline`), not to an HTTP verb
anything on the network can send twice.
"""
import hmac
import ipaddress
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from pydantic import BeforeValidator, ConfigDict, Field, StrictInt, create_model

from config import (
    ALLOW_NO_AUTH,
    ALLOWED_ORIGINS,
    API_BIND_HOST,
    API_KEY,
    CALIBRATOR_PATH,
    CAT_COLS,
    CATEGORICAL_LEVELS,
    DAILY_DATA_PATH,
    FEATURE_BOUNDS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
    MODEL_META_PATH,
    MODEL_PATH,
    SHAP_TOP_N_FEATURES,
    STUDENT_INFO,
)
from pipeline.daily_pipeline import score_students
from src.data.features import build_serving_frame
from src.data.loader import load_daily_students
from src.data.preprocess import cast_categoricals, daily_process
from src.data.validation import DataValidationError, require_no_nulls
from src.explainer.shap_explainer import create_explainer, explain_customers
from src.logging_setup import configure_logging, new_request_id, request_id_var
from src.model.calibrate import churn_proba, load_calibrator
from src.model.load import check_meta_matches_config, load_meta, load_model
from src.serialization import to_external

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]


# --- Authentication ---------------------------------------------------------
def _is_loopback(host: str) -> bool:
    """True only for an address that cannot be reached from another machine.

    An empty or unparseable host is not loopback: the caller treats "unknown" as
    public, so a typo in API_BIND_HOST must never read as safe.
    """
    host = (host or "").strip().strip("[]")
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class AuthConfigurationError(RuntimeError):
    """The process would serve data without authentication. Refuse to start."""


def check_auth_configuration(
    api_key: str | None = API_KEY,
    *,
    allow_no_auth: bool = ALLOW_NO_AUTH,
    bind_host: str = API_BIND_HOST,
) -> None:
    """Raise unless this process is safe to serve: authenticated, or provably local.

    Every response body here contains student ids and 24 behavioural features for
    (mostly) minors, so "no key configured" must not degrade into "no key needed".
    The only ways through are a real key, or an operator typing
    EOAI_ALLOW_NO_AUTH=1 for a loopback-bound development server.
    """
    if api_key is not None:
        return
    if not allow_no_auth:
        raise AuthConfigurationError(
            "API_KEY is not set. Refusing to start an unauthenticated API. Set API_KEY, "
            "or for local development only set EOAI_ALLOW_NO_AUTH=1 with a loopback "
            "API_BIND_HOST."
        )
    if not _is_loopback(bind_host):
        raise AuthConfigurationError(
            f"EOAI_ALLOW_NO_AUTH is set but API_BIND_HOST={bind_host!r} is not a loopback "
            "address. The no-auth opt-out is for local development only; set API_KEY "
            "instead."
        )
    logger.warning(
        "EOAI_ALLOW_NO_AUTH=1 and API_BIND_HOST=%s - running WITHOUT authentication "
        "(local development only)",
        bind_host,
    )


# --- Startup / shutdown -----------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    check_auth_configuration()

    app.state.model = None
    app.state.explainer = None
    app.state.calibrator = None
    app.state.meta = {}
    # Kept on state for debugging from a console; deliberately in no response body -
    # it is a filesystem path and a stack of internal names.
    app.state.load_error = None
    try:
        model = load_model(MODEL_PATH)
        explainer = create_explainer(model)
        calibrator = load_calibrator(CALIBRATOR_PATH)
        meta = load_meta(MODEL_META_PATH) or {}
        check_meta_matches_config(meta)
    except Exception as e:  # noqa: BLE001 - deliberately degrade instead of crash
        app.state.load_error = str(e)
        logger.exception("startup load failed - API will report degraded and refuse to score")
    else:
        # Published only once all four loaded. `app.state.model` used to be assigned
        # first, so a failure in the explainer, the calibrator or the meta left
        # /health answering "ok" while every scoring call 500ed - and because
        # docker-compose's healthcheck reads /health, a permanently broken container
        # stayed "healthy" forever.
        app.state.model = model
        app.state.explainer = explainer
        app.state.calibrator = calibrator
        app.state.meta = meta
        logger.info("model loaded from %s", MODEL_PATH)
    yield


# The three generated-documentation routes are re-registered below behind the API
# key. FastAPI's built-in ones take no dependencies, and /openapi.json alone lists
# every endpoint, every field name and every bound - a map of the data for anyone
# who can reach the port.
app = FastAPI(
    title="EOAI Churn Early Warning API",
    version="2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _log_path(request: Request) -> str:
    """The route template, never the values substituted into it.

    `GET /predict/STU300001` wrote a plain-text student id into the access log -
    unrotated, unmasked, and for people who are mostly minors. `/predict/{student_id}`
    tells an operator everything an access log is for.
    """
    template = getattr(request.scope.get("route"), "path", None)
    if template:
        return template
    # Nothing matched (a 404), so the path is whatever the caller typed and may still
    # contain an id. Keep the first segment only.
    segments = [segment for segment in request.url.path.split("/") if segment]
    if not segments:
        return "/"
    return "/" + segments[0] + ("/..." if len(segments) > 1 else "")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Set before call_next: the downstream app runs in a child task, which copies the
    # context as it is now - so the access line and any traceback logged while serving
    # this request share one id.
    request_id_var.set(new_request_id())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # An unhandled exception propagates past this middleware (Starlette's
        # ServerErrorMiddleware sits further out), so without this branch the one
        # request that needs an access line to match its traceback would not get one.
        logger.info(
            "%s %s -> 500 (%.0f ms)",
            request.method,
            _log_path(request),
            (time.perf_counter() - started) * 1000,
        )
        raise
    logger.info(
        "%s %s -> %s (%.0f ms)",
        request.method,
        _log_path(request),
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Anything not raised as an HTTPException is a server bug: log it, return 500."""
    logger.exception("unhandled error on %s %s", request.method, _log_path(request))
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# --- Dependencies --------------------------------------------------------
def require_api_key(x_api_key: str | None = Header(default=None)):
    """Reject the request unless it carries the right X-API-Key header.

    Only skipped when `API_KEY` is unset, which `check_auth_configuration` has
    already established means an explicitly opted-in local development server.
    Compared with `hmac.compare_digest` so the comparison time does not leak the
    number of leading bytes an attacker got right.
    """
    if API_KEY is None:
        return
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode("utf-8"), API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


def readiness(state) -> dict[str, bool]:
    """What is loaded. Scoring needs all four, so readiness is all four.

    The calibrator is in here because `churn_proba` falls back to CatBoost's raw
    score when it is None: the service would keep answering, with wrong numbers.
    """
    return {
        "model": state.model is not None,
        "explainer": state.explainer is not None,
        "calibrator": state.calibrator is not None,
        "meta": bool(state.meta),
    }


def get_model(request: Request):
    """The scoring gate: fail closed unless everything scoring needs is loaded."""
    missing = [name for name, loaded in readiness(request.app.state).items() if not loaded]
    if missing:
        # Component names only. The load error is a filesystem path, and it is already
        # in the startup log where an operator can read it.
        raise HTTPException(
            status_code=503, detail=f"service not ready, not loaded: {', '.join(missing)}"
        )
    return request.app.state.model


# --- Request schema ----------------------------------------------------
def _reject_bool(value):
    """pydantic's lax mode turns True into 1.0. A boolean in a numeric field is a
    client bug, never a measurement, and silently scoring it as 1.0 hides it."""
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    return value


# Bounded real-valued input. `allow_inf_nan=False` matters because json.loads (which
# is what Starlette parses the body with) accepts the literals NaN and Infinity, and
# a NaN reaches CatBoost as a missing value rather than as an error.
def _numeric_field(low, high):
    return (
        Annotated[float, BeforeValidator(_reject_bool)],
        Field(..., ge=low, le=high, allow_inf_nan=False),
    )


def _field_spec(feature: str):
    """The (type, Field) pair for one feature of the POST /predict body."""
    if feature in CAT_COLS:
        # Literal, not str: an unlisted category is refused here rather than hashed
        # into a plausible-looking probability by CatBoost. It also bounds the length
        # of an accepted value to the longest configured level.
        return (Literal[tuple(CATEGORICAL_LEVELS[feature])], ...)
    low, high = FEATURE_BOUNDS[feature]
    if feature in INTEGER_FEATURES or feature in FLAG_FEATURES:
        # StrictInt: rejects 2.7 tickets, and rejects True, which lax int accepts.
        return (StrictInt, Field(..., ge=low, le=high))
    return _numeric_field(low, high)


# POST /predict body: exactly the columns in config.FEATURES, nothing else.
# `extra="forbid"` because src/data/validation.py rejects an extra column for a
# reason - it would become model feature #25 - and the API dropping it silently
# contradicted that on the one path where the client can still be told.
RawCustomerIn = create_model(
    "RawCustomerIn",
    __config__=ConfigDict(extra="forbid"),
    **{feature: _field_spec(feature) for feature in FEATURES},
)


# Enough to point at the problem, few enough that a broken client cannot turn one
# 422 body into a log-filling wall of text.
_MAX_REPORTED_VALIDATION_ERRORS = 5


def _summarise_validation_errors(errors: list[dict]) -> str:
    """Flatten pydantic's list of error objects into one sentence.

    API_CONTRACT.md promises `{"detail": "<string>"}` for every 4xx; FastAPI's
    default returns a list of objects instead, so a client that renders `detail`
    shows "[object Object]". Only the field name and the constraint go in - never the
    value that was sent, which on this API is a student's data.
    """
    parts = []
    for error in errors[:_MAX_REPORTED_VALIDATION_ERRORS]:
        location = ".".join(
            str(item) for item in error.get("loc", ()) if item not in ("body", "query")
        )
        parts.append(f"{location or 'request'}: {error.get('msg', 'invalid value')}")
    summary = "; ".join(parts)
    remaining = len(errors) - _MAX_REPORTED_VALIDATION_ERRORS
    if remaining > 0:
        summary += f" (and {remaining} more)"
    return summary


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422, content={"detail": _summarise_validation_errors(exc.errors())}
    )


# --- Scoring helper ------------------------------------------------------
def _score(request: Request, X: pd.DataFrame, top_n: int):
    """Score one row. `X` must contain exactly config.FEATURES."""
    model = get_model(request)
    X = cast_categoricals(X[FEATURES])
    proba = float(churn_proba(model, X, request.app.state.calibrator)[0])
    reasons = explain_customers(request.app.state.explainer, X, top_n=top_n)[0]
    return proba, [{"feature": r["feature"], "impact": r["impact"]} for r in reasons]


# --- Generated documentation (behind the key) -------------------------------
@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_api_key)])
def openapi_schema():
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_api_key)])
def swagger_ui():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_api_key)])
def redoc_ui():
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


# --- Endpoints -------------------------------------------------------
@app.get("/health")
def health(request: Request):
    """Readiness, not liveness: docker-compose's healthcheck reads this.

    "ok" means every one of the four components scoring needs is loaded, not just the
    model - anything less and the scoring endpoints return 503, so reporting "ok"
    would be a lie a container orchestrator acts on.

    `components` is the only detail: no load error and no file path, because this is
    the one endpoint anyone who can reach the port may call. The reason is in the
    startup log.
    """
    components = readiness(request.app.state)
    status = "ok" if all(components.values()) else "degraded"
    return to_external({"status": status, "components": components})


@app.post("/predict", dependencies=[Depends(require_api_key)])
def predict_raw(payload: RawCustomerIn, request: Request):
    try:
        proba, reasons = _score(
            request, pd.DataFrame([payload.model_dump()]), SHAP_TOP_N_FEATURES
        )
    except (KeyError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=f"invalid input: {e}")
    return to_external({"churn_probability": proba, "top_reasons": reasons})


@app.get("/predict/{student_id}", dependencies=[Depends(require_api_key)])
def predict_by_student_id(student_id: str, request: Request):
    try:
        daily = load_daily_students(DAILY_DATA_PATH)
    except (FileNotFoundError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    match = daily[daily[_ID_COLUMN].astype(str) == str(student_id)]
    if match.empty:
        # The id is not echoed back: this body is logged by proxies and rendered by
        # clients, and a reflected identifier is one more place it can end up.
        raise HTTPException(status_code=404, detail="student_id not found in daily data")

    imputation_values = request.app.state.meta.get("imputation_values", {})
    try:
        engineered = build_serving_frame(match.iloc[[0]], imputation_values)
        require_no_nulls(engineered, FEATURES)
        customer_info, X = daily_process(engineered)
        proba, reasons = _score(request, X, SHAP_TOP_N_FEATURES)
    except (KeyError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=f"invalid input: {e}")

    # customer_info carries the STUDENT_INFO columns, which are allowed to be null:
    # nothing imputes them and nothing should. to_external is what turns that null
    # into JSON `null` instead of a NaN Starlette refuses to encode.
    return to_external(
        {
            **customer_info.iloc[0].to_dict(),
            "churn_probability": proba,
            "features": X.iloc[0],
            "top_reasons": reasons,
        }
    )


def _resolve_threshold(request: Request, threshold: float | None) -> float:
    """Explicit `?threshold=` wins; otherwise use the value chosen during training."""
    if threshold is not None:
        return threshold
    return request.app.state.meta.get("chosen_threshold", 0.5)


# A probability threshold outside [0, 1] is meaningless, and it was not merely
# accepted: `?threshold=nan` serialised to a 500 (Starlette encodes with
# allow_nan=False), and `?threshold=-inf` ran SHAP over the entire dataset before
# failing the same way. Cheaper to refuse the request than to compute it.
_ThresholdQuery = Annotated[
    float | None, Query(ge=0, le=1, allow_inf_nan=False, description="0 <= threshold <= 1")
]


@app.get("/students", dependencies=[Depends(require_api_key)])
def list_scored_students(request: Request, threshold: _ThresholdQuery = None):
    """Read-only scoring. Same work as POST /run-daily-pipeline, no side effects.

    Nothing is written to the alert log, so `status` (new / still_at_risk) is not
    part of the response - that classification only means something relative to a
    recorded run. Use `?threshold=0` to get every student scored.
    """
    get_model(request)
    meta = request.app.state.meta
    chosen_threshold = _resolve_threshold(request, threshold)
    try:
        result = score_students(
            DAILY_DATA_PATH,
            model=request.app.state.model,
            explainer=request.app.state.explainer,
            calibrator=request.app.state.calibrator,
            imputation_values=meta.get("imputation_values", {}),
            threshold=chosen_threshold,
        )
    except (FileNotFoundError, DataValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:  # data source misconfigured (e.g. DATA_SOURCE=db, no URL)
        raise HTTPException(status_code=503, detail=str(e))

    return to_external(
        {
            "count": len(result),
            "threshold": chosen_threshold,
            "students": result,
        }
    )


# What GET /metrics may publish. An allow-list, so the safe default for any new key
# in model_meta.json is "not returned": the file also holds absolute filesystem
# paths, the training data's sha256, the imputation medians (training-set
# statistics), the model hyperparameters and tree count, and a false-negative
# breakdown by class / city / plan - which is a map of where the model is blind,
# per segment. None of that is anything the dashboard asks for.
METRICS_PUBLIC_FIELDS = (
    "is_synthetic_data",
    "trained_at",
    "chosen_threshold",
    "calibration_method",
    "data_rows",
    "metrics",
    "cv_auc_mean",
    "cv_auc_std",
    "baseline_metrics",
)


@app.get("/metrics", dependencies=[Depends(require_api_key)])
def latest_metrics(request: Request):
    """Return the published subset of the currently loaded model's metadata."""
    meta = request.app.state.meta
    if not meta:
        raise HTTPException(
            status_code=404,
            detail="no trained-model metadata found; run running_train_pipeline.py first",
        )
    return to_external(
        {field: meta[field] for field in METRICS_PUBLIC_FIELDS if field in meta}
    )
