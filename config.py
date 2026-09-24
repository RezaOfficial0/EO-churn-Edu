"""Every tunable setting for the churn system lives here.

Nothing else in the codebase hard-codes a path, a column name, a hyperparameter,
or a threshold. Change it here and the whole pipeline follows.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # read .env (if present) into the environment


# gather secret env statements and pass a str env with a properate Format
# env(abc/n) -> abc
#env(" " , "" , "/n") -> None
def _env_secret(name: str) -> str | None:
    return (os.getenv(name) or "").strip() or None



# --- File locations -----------------------------------------------------------
# Anchored to this file, so they resolve the same from any working directory
# (repo root, Docker WORKDIR, a systemd unit, ...).
BASE_DIR = Path(__file__).resolve().parent

RAW_DATA_PATH = str(BASE_DIR / "data" / "mentorluk_churn_veriseti.csv")
TRAIN_DATA_PATH = str(BASE_DIR / "data" / "updated_data.csv")
DAILY_DATA_PATH = str(BASE_DIR / "data" / "daily_data.csv")
DAILY_ALERTS_PATH = str(BASE_DIR / "data" / "daily_alerts.csv")

MODEL_PATH = str(BASE_DIR / "saved_models" / "catboost_churn_v1.cbm")
MODEL_META_PATH = str(BASE_DIR / "saved_models" / "model_meta.json")
CALIBRATOR_PATH = str(BASE_DIR / "saved_models" / "calibrator.joblib")
METRICS_DIR = str(BASE_DIR / "metrics")


# --- Columns ----------------------------------------------------------------
# Id columns: passed straight through to API responses, never given to the model.
STUDENT_INFO = [
    "student_id",
    "enrollment_date",
]

# The exact columns the model is trained and served on, in this order.
FEATURES = [
    "grade",
    "track",
    "city_tier",
    "parent_involvement",
    "plan_type",
    "monthly_value_try",
    "tenure_months",
    "program_adherence_rate",
    "weekly_study_hours_planned",
    "weekly_study_hours_actual",
    "mentor_contact_freq_per_month",
    "days_since_last_contact",
    "message_response_time_hours",
    "late_response_count_30d",
    "trial_exam_count_total",
    "trial_exam_avg_net",
    "trial_exam_score_trend",
    "missed_trial_exam_count",
    "payment_delay_days_avg",
    "support_ticket_count_90d",
    "satisfaction_survey_score",
    "days_to_next_exam",
    "weekly_study_hours_actual_missing",
    "satisfaction_missing",
]

# Which of FEATURES are categorical. CatBoost handles these natively, by name.
CAT_COLS = [
    "grade",
    "track",
    "city_tier",
    "parent_involvement",
    "plan_type",
]

# The label column.
TARGET_FEATURE = "churn"


# How many months each plan's price covers.
#
# `monthly_fee_try` in the raw export is the PRICE OF THE PLAN, not a monthly
# amount: an "Aylık" row is ~1.800 TL, a "Yıllık" row ~16.730 TL. Fed to the model
# as it stands it is three non-overlapping ranges - a perfect proxy for plan_type
# and nothing more, so the model was given the same fact twice. Divided by the
# months it covers it becomes what the name always promised: what a student is
# worth per month (~1.800 / ~1.634 / ~1.394 by plan). Those ranges DO overlap, so
# it carries information plan_type does not - and it is the number any
# revenue-weighted prioritisation needs.
#
# Per client, like FEATURES: a plan name missing from this table falls back to 1
# (the price is treated as monthly) with a warning, rather than failing the run.
PLAN_MONTHS = {
    "Aylık": 1,
    "3 Aylık": 3,
    "Yıllık": 12,
}


# --- Model hyperparameters --------------------------------------------------
MODEL_PARAMS = {
    "iterations": 300,
    "depth": 4,
    "learning_rate": 0.05,
}


# --- Probability calibration -------------------------------------------------
# With auto_class_weights="Balanced", CatBoost's raw predict_proba is not a real
# probability. A calibrator maps it onto one. Two methods, and the choice is a
# real trade-off rather than a detail:
#
#   "sigmoid"  - Platt scaling: one logistic curve fitted on the raw score. Smooth
#                and strictly increasing, so every student keeps a distinct score
#                and the list can be ranked.
#   "isotonic" - a step function. It fits the validation set more closely, but it
#                maps whole intervals of raw score onto a single value: measured on
#                this data it collapsed 677 distinct test scores to 24, put four of
#                eight daily at-risk students on the identical probability, and cost
#                0.038 PR-AUC. It needs considerably more validation data than we
#                have to be worth that.
#
# Both are monotone, so neither invents a ranking - isotonic only destroys one, by
# creating ties. Whichever is chosen, the training summary reports calibrated and
# raw ROC-AUC / PR-AUC side by side, so the cost is visible.
CALIBRATION_METHOD = "sigmoid"


# --- Alert threshold selection --------------------------------------------
# Relative cost of the two mistakes. A false alarm wastes one mentor outreach;
# a missed churn loses a student. Training picks the probability threshold that
# minimises   false_alarms * false_alarm  +  missed_churns * missed_churn.
# Raise "missed_churn" to alert more aggressively (higher recall, lower precision).
DECISION_COST = {
    "false_alarm": 1,
    "missed_churn": 3,
}

# "Mentors can contact this many students per run" - precision@K is reported for it.
PRECISION_AT_K = 20


# --- Data quality ---------------------------------------------------------
# A single column with more than this fraction of nulls fails validation.
MAX_NULL_RATIO_PER_COLUMN = 0.05


# --- Explanations -------------------------------------------------------
SHAP_TOP_N_FEATURES = 3


# --- API input ranges -------------------------------------------------
# Accepted (min, max) for each numeric field of POST /predict. A value outside
# its range returns HTTP 422 (this is what stops monthly_value_try = 1e18).
FEATURE_BOUNDS = {
    "monthly_value_try": (0, 1_000_000),
    "tenure_months": (0, 600),
    "program_adherence_rate": (0, 1),
    "weekly_study_hours_planned": (0, 168),
    "weekly_study_hours_actual": (0, 168),
    "mentor_contact_freq_per_month": (0, 300),
    "days_since_last_contact": (0, 3650),
    "message_response_time_hours": (0, 8760),
    "late_response_count_30d": (0, 1000),
    "trial_exam_count_total": (0, 10_000),
    "trial_exam_avg_net": (0, 500),
    "trial_exam_score_trend": (-100, 100),
    "missed_trial_exam_count": (0, 10_000),
    "payment_delay_days_avg": (0, 3650),
    "support_ticket_count_90d": (0, 10_000),
    "satisfaction_survey_score": (1, 5),
    "days_to_next_exam": (0, 3650),
    "weekly_study_hours_actual_missing": (0, 1),
    "satisfaction_missing": (0, 1),
}


# The accepted values of each categorical field. CatBoost does not reject an unseen
# category, it hashes it and returns a confident-looking probability - so
# {"plan_type": "banana"} used to score 200 and nothing anywhere said it was
# nonsense. The API is the only layer that can refuse it, and it can only refuse
# what is written down. Per client, like FEATURES and FEATURE_LABELS.
CATEGORICAL_LEVELS = {
    "grade": ["11. Sınıf", "12. Sınıf", "Mezun"],
    "track": ["Sayısal", "Eşit Ağırlık", "Sözel", "Dil"],
    "city_tier": ["Tier 1 (Büyükşehir)", "Tier 2", "Tier 3"],
    "parent_involvement": ["Düşük", "Orta", "Yüksek"],
    "plan_type": ["Aylık", "3 Aylık", "Yıllık"],
}

# A category name longer than this is a malformed request, not a level: checked on
# the config table rather than per request, because the API validates categoricals
# against the list above and never sees a value that is not in it.
MAX_CATEGORY_LENGTH = 64

# Numeric features that are whole numbers by nature - counters and day counts.
# "2.7 support tickets in 90 days" is not a measurement the model was trained on,
# and accepting it hides a broken client instead of reporting it.
INTEGER_FEATURES = [
    "days_since_last_contact",
    "late_response_count_30d",
    "trial_exam_count_total",
    "missed_trial_exam_count",
    "support_ticket_count_90d",
    "days_to_next_exam",
]

# 0/1 indicator columns produced by feature engineering. There is no such thing as
# a survey that is 0.5 missing.
FLAG_FEATURES = [
    "weekly_study_hours_actual_missing",
    "satisfaction_missing",
]


# --- Notifications ----------------------------------------------------------
# Human-readable Turkish label for each feature, used in the daily alert message
# a mentor actually reads. "days_since_last_contact (+0.91)" means nothing to
# them; "Son iletisimden bu yana (gun): 41" does.
#
# This is per client: a new customer with different columns replaces this table
# (and FEATURES above) - no other file changes.
FEATURE_LABELS = {
    "grade": "Sınıf",
    "track": "Alan",
    "city_tier": "Şehir kademesi",
    "parent_involvement": "Veli ilgisi",
    "plan_type": "Paket",
    "monthly_value_try": "Aylık değer (TL)",
    "tenure_months": "Programdaki süresi (ay)",
    "program_adherence_rate": "Program uyum oranı",
    "weekly_study_hours_planned": "Planlanan haftalık çalışma (saat)",
    "weekly_study_hours_actual": "Gerçekleşen haftalık çalışma (saat)",
    "mentor_contact_freq_per_month": "Aylık mentor görüşme sayısı",
    "days_since_last_contact": "Son iletişimden bu yana (gün)",
    "message_response_time_hours": "Mesaja yanıt süresi (saat)",
    "late_response_count_30d": "Son 30 günde geç yanıt",
    "trial_exam_count_total": "Toplam deneme sınavı",
    "trial_exam_avg_net": "Deneme ortalama net",
    "trial_exam_score_trend": "Deneme net eğilimi",
    "missed_trial_exam_count": "Kaçırılan deneme sayısı",
    "payment_delay_days_avg": "Ortalama ödeme gecikmesi (gün)",
    "support_ticket_count_90d": "Son 90 günde destek talebi",
    "satisfaction_survey_score": "Memnuniyet puanı (1-5)",
    "days_to_next_exam": "Sonraki sınava kalan gün",
    "weekly_study_hours_actual_missing": "Çalışma saati verisi eksik",
    "satisfaction_missing": "Memnuniyet anketi doldurulmamış",
}



#CLIENT LABELS____________________________________________________________
CLIENT_ID ="edu-demo"
CLIENT_DOMAIN ="education" #education / saas / fintech / other
PROGRAM_NAME = "EO Mentorluk Programı"
CAMPAIGN_AUDIENCE = "relationship_owner" #client / team / relationship_owner
CAMPAIGN_LANGUAGE = "tr"  #tr / en
CAMPAIGN_COOLDOWN_DAYS = 30 #Aynı öğrenciye tekrar kampanya üretmeden önce beklenecek gün.
CAMPAIGN_MAX_STUDENTS_PER_RUN = 50 	#Tek çalışmada LLM'e gönderilecek maksimum öğrenci.







# --- Config consistency ------------------------------------------------------
# FEATURES is the list a client onboarding edits, and three other tables have to
# keep up with it: bounds for API validation, Turkish labels for the alert
# message, and the subset that is categorical. Getting them out of step is the
# single most likely onboarding mistake, and without this check it surfaces far
# from its cause - a renamed feature raises KeyError inside the pydantic model
# factory in api/main.py, which says nothing about config.py.
def _validate_feature_config() -> None:
    problems = []

    duplicates = sorted({name for name in FEATURES if FEATURES.count(name) > 1})
    if duplicates:
        problems.append(f"FEATURES contains duplicates: {duplicates}")

    unknown_cat = sorted(set(CAT_COLS) - set(FEATURES))
    if unknown_cat:
        problems.append(f"CAT_COLS names that are not in FEATURES: {unknown_cat}")

    # Categorical features are passed to CatBoost by name and never bounds-checked,
    # so only the numeric ones need an entry in FEATURE_BOUNDS.
    numeric = [name for name in FEATURES if name not in CAT_COLS]
    missing_bounds = [name for name in numeric if name not in FEATURE_BOUNDS]
    if missing_bounds:
        problems.append(f"FEATURES without a FEATURE_BOUNDS entry: {missing_bounds}")

    missing_labels = [name for name in FEATURES if name not in FEATURE_LABELS]
    if missing_labels:
        problems.append(f"FEATURES without a FEATURE_LABELS entry: {missing_labels}")

    # The API builds a Literal[...] per categorical from this table, so a missing
    # entry is not a cosmetic gap: it is a field that would accept any string.
    if set(CATEGORICAL_LEVELS) != set(CAT_COLS):
        problems.append(
            f"CATEGORICAL_LEVELS keys {sorted(CATEGORICAL_LEVELS)} do not match "
            f"CAT_COLS {sorted(CAT_COLS)}"
        )
    for column, levels in CATEGORICAL_LEVELS.items():
        if not levels:
            problems.append(f"CATEGORICAL_LEVELS[{column!r}] is empty")
        too_long = [level for level in levels if len(level) > MAX_CATEGORY_LENGTH]
        if too_long:
            problems.append(
                f"CATEGORICAL_LEVELS[{column!r}] has level(s) longer than "
                f"{MAX_CATEGORY_LENGTH} characters: {too_long}"
            )

    typed_numeric = INTEGER_FEATURES + FLAG_FEATURES
    unknown_typed = [name for name in typed_numeric if name not in numeric]
    if unknown_typed:
        problems.append(
            f"INTEGER_FEATURES / FLAG_FEATURES names that are not numeric FEATURES: "
            f"{unknown_typed}"
        )
    bad_flag_bounds = [
        name for name in FLAG_FEATURES if FEATURE_BOUNDS.get(name) != (0, 1)
    ]
    if bad_flag_bounds:
        problems.append(f"FLAG_FEATURES without (0, 1) bounds: {bad_flag_bounds}")

    # Not fatal on its own, but it is always a leftover from a rename.
    stale_bounds = sorted(set(FEATURE_BOUNDS) - set(FEATURES))
    if stale_bounds:
        problems.append(f"FEATURE_BOUNDS entries for features that no longer exist: {stale_bounds}")

    overlap = sorted(set(STUDENT_INFO) & set(FEATURES))
    if overlap:
        problems.append(f"columns in both STUDENT_INFO and FEATURES: {overlap}")

    if TARGET_FEATURE in FEATURES:
        problems.append(f"TARGET_FEATURE {TARGET_FEATURE!r} is also in FEATURES (label leakage)")

    if problems:
        raise ValueError(
            "config.py is inconsistent - fix these before anything else runs:\n  - "
            + "\n  - ".join(problems)
        )


_validate_feature_config()


# Shown at the top of every alert message, so a mentor knows which programme the
# alert is about when one inbox serves several clients.
NOTIFY_TITLE = os.environ.get("NOTIFY_TITLE", "EO-Churn — Günlük Risk Uyarısı")


# --- Environment-driven settings (see .env.example) ----------------
DATA_SOURCE = os.getenv("DATA_SOURCE", "csv")  # "csv" or "db"
API_KEY = _env_secret("API_KEY") or None
DATABASE_URL = _env_secret("DATABASE_URL") or None


# --- API authentication -----------------------------------------------------
# The address this API is reachable on: uvicorn's `--host`, or the address Docker
# publishes the container port on. Neither is visible from inside the ASGI app, so
# the deployment declares it here - "can another machine reach this port?" is
# exactly what decides whether a missing API_KEY may be tolerated, and the app has
# to be able to answer that at startup.
#
# The default is deliberately the UNSAFE answer: unset means "we do not know", and
# an unknown address is treated as public. A wrong guess in the other direction
# would serve every student's record to the network.
API_BIND_HOST = os.environ.get("API_BIND_HOST", "0.0.0.0").strip() or "0.0.0.0"

# The one way to run without authentication. It has to be typed out on purpose,
# which an empty API_KEY in a shipped .env template never is - that is how the
# documented setup path ended up producing an open service.
ALLOW_NO_AUTH = os.environ.get("EOAI_ALLOW_NO_AUTH", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


# Browser origins allowed to call the API (CORS).
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

# --- Notification channels --------------------------------------------------
# Which channels the daily alert goes to. Comma-separated, any of:
#   telegram, email, webhook
# Empty (the default) means print to stdout only - which is what you want on a
# developer machine, so a test run never messages a real person.
NOTIFY_CHANNELS = [
    channel.strip().lower()
    for channel in os.environ.get("NOTIFY_CHANNELS", "").split(",")
    if channel.strip()
]








# Telegram: talk to @BotFather to create a bot and get the token; the chat id is
# the conversation (or group) the bot posts into.
TELEGRAM_BOT_TOKEN = _env_secret("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID = _env_secret("TELEGRAM_CHAT_ID") or None

# Email over SMTP. SMTP_TO is comma-separated.
SMTP_HOST = os.environ.get("SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = _env_secret("SMTP_USER") or None
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or None
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USER
SMTP_TO = [
    address.strip()
    for address in os.environ.get("SMTP_TO", "").split(",")
    if address.strip()
]
# True for STARTTLS on port 587 (the common case); False for implicit SSL on 465.
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "true").lower() not in {"false", "0", "no"}

# Webhook that scripts/send_daily_alerts.py posts new at-risk students to
# (Slack / Discord "incoming webhook" URL, or anything accepting {"text": ...}).
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL") or None




#LLM______________________________________________________-
LLM_SERVICE_URL = os.environ.get("LLM_SERVICE_URL") or None
LLM_SERVICE_KEY = os.environ.get("LLM_SERVICE_KEY") or None
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", 180))
CAMPAIGN_FEATURE =os.environ.get("CAMPAIGN_FEATURE", "false").lower() not in {"false", "0", "no"}
ALERT_WEBHOOK_URL = _env_secret("ALERT_WEBHOOK_URL") or None
