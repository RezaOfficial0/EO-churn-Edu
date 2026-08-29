from pipeline.training_pipeline import run_training_pipeline
from config import FEATURES , STUDENT_INFO , TARGET_FEATURE , TRAIN_DATA_PATH , CAT_COLS , MODEL_PATH

run_training_pipeline(TRAIN_DATA_PATH , MODEL_PATH)