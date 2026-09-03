"""Rebuild data/updated_data.csv from the raw export, reproducibly.

    python scripts/build_training_data.py

The output is what `data/updated_data.csv` already contains; running this after a
new raw export keeps the training data in sync with the recipe in
`src/data/features.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import RAW_DATA_PATH, TRAIN_DATA_PATH
from src.data.loader import data_loader
from src.data.features import build_training_frame
from src.logging_setup import configure_logging


def main() -> None:
    configure_logging()
    raw = data_loader(RAW_DATA_PATH)
    engineered, _learned = build_training_frame(raw)

    # Keep the raw column order, then append the new engineered columns.
    raw_columns = list(raw.columns)
    new_columns = [c for c in engineered.columns if c not in raw_columns]
    engineered = engineered[raw_columns + new_columns]

    engineered.to_csv(TRAIN_DATA_PATH, index=False)
    print(f"wrote {len(engineered)} rows x {engineered.shape[1]} cols -> {TRAIN_DATA_PATH}")


if __name__ == "__main__":
    main()
