"""Entry point: train the churn model from the configured training CSV.

Run it directly:

    python running_train_pipeline.py
"""
from config import TRAIN_DATA_PATH, MODEL_PATH
from pipeline.training_pipeline import run_training_pipeline

if __name__ == "__main__":
    run_training_pipeline(TRAIN_DATA_PATH, MODEL_PATH)
