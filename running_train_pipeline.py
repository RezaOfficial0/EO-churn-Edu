"""Entry point: train the churn model from the raw dataset.

    python running_train_pipeline.py
"""
from config import MODEL_PATH, RAW_DATA_PATH
from pipeline.training_pipeline import run_training_pipeline

if __name__ == "__main__":
    run_training_pipeline(RAW_DATA_PATH, MODEL_PATH)
