from pipeline.training_pipeline import run_training_pipeline
from config import TRAIN_DATA_PATH, MODEL_PATH
from src.logging_config import setup_logging


if __name__ == "__main__":
    setup_logging()
    run_training_pipeline(TRAIN_DATA_PATH, MODEL_PATH)
