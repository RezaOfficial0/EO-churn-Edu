"""Every tunable setting for the churn system lives here.

Nothing else in the codebase hard-codes a path, a column name, a hyperparameter,
or a threshold. Change it here and the whole pipeline follows.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # read .env (if present) into the environment


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
    "monthly_fee_try",
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


# --- Model hyperparameters --------------------------------------------------
MODEL_PARAMS = {
    "iterations": 300,
    "depth": 4,
    "learning_rate": 0.05,
}


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
# its range returns HTTP 422 (this is what stops monthly_fee_try = 1e18).
FEATURE_BOUNDS = {
    "monthly_fee_try": (0, 1_000_000),
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
    "monthly_fee_try": "Aylık ücret (TL)",
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









# Shown at the top of every alert message, so a mentor knows which programme the
# alert is about when one inbox serves several clients.
NOTIFY_TITLE = os.environ.get("NOTIFY_TITLE", "EO-Churn — Günlük Risk Uyarısı")


# --- Environment-driven settings (see .env.example) ----------------
DATA_SOURCE = os.getenv("DATA_SOURCE", "csv")  # "csv" or "db"
API_KEY = os.environ.get("API_KEY") or None
DATABASE_URL = os.getenv("DATABASE_URL") or None


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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or None

# Email over SMTP. SMTP_TO is comma-separated.
SMTP_HOST = os.environ.get("SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER") or None
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
