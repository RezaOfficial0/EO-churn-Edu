import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()



## PATHS
BASE_DIR = Path(__file__).resolve().parent # root project
TRAIN_DATA_PATH = str(BASE_DIR / "data" / "updated_data.csv") # Training data path
DAILY_DATA_PATH = str(BASE_DIR / "data" / "daily_data.csv")  # for now using local Data
MODEL_PATH = str(  BASE_DIR / "saved_models" / "catboost_churn_v1.cbm")



## COLUMNS
STUDENT_INFO = [
    "student_id",
    "enrollment_date"
]

FEATURES = ['grade', 'track', 'city_tier', 'parent_involvement',
       'plan_type', 'monthly_fee_try','tenure_months',
       'program_adherence_rate', 'weekly_study_hours_planned',
       'weekly_study_hours_actual', 'mentor_contact_freq_per_month',
       'days_since_last_contact', 'message_response_time_hours',
       'late_response_count_30d', 'trial_exam_count_total',
       'trial_exam_avg_net', 'trial_exam_score_trend',
       'missed_trial_exam_count', 'payment_delay_days_avg',
       'support_ticket_count_90d', 'satisfaction_survey_score',
       'days_to_next_exam', 'weekly_study_hours_actual_missing', 'satisfaction_missing']

TARGET_FEATURE = ["churn"]
CAT_COLS = ["grade", "track", "city_tier", "parent_involvement", "plan_type"]

SHAP_TOP_N_FEATURES = 3





