"""Model 3: Deep ANN churn classification pipeline (RnD)."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from config import (
    BASE_DIR,
    CAT_COLS,
    DECISION_COST,
    FEATURES,
    METRICS_DIR,
    PRECISION_AT_K,
    STUDENT_INFO,
    TARGET_FEATURE,
    TRAIN_DATA_PATH,
)
from src.data.features import build_training_frame
from src.data.loader import data_loader
from src.data.preprocess import split_features_target, split_train_val_test
from src.data.validation import validate
from src.logging_setup import configure_logging
from src.model.evaluate import evaluate_model, false_negative_breakdown
from src.model.threshold import select_threshold

logger = logging.getLogger(__name__)

SAVED_MODELS_DIR = Path(BASE_DIR / "saved_models")


class ChurnDataset(Dataset):
    """Dataset wrapper for tabular tensor inputs and labels."""
    def __init__(self, X_data: np.ndarray, y_data: Any):
        self.X = torch.tensor(X_data, dtype=torch.float32)
        y_arr = y_data.values if hasattr(y_data, "values") else y_data
        self.y = torch.tensor(y_arr, dtype=torch.float32).unsqueeze(1)

    def __len__(self) -> int:
        return len(self.X)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.X[idx], self.y[idx]


class ChurnANN(nn.Module):
    """2-Layer MLP with BatchNorm and Dropout for Churn Classification."""
    def __init__(self, input_dim: int):
        super(ChurnANN, self).__init__()
        self.fc1 = nn.Linear(input_dim, 64)
        self.bn1 = nn.BatchNorm1d(64)
        self.relu = nn.ReLU()
        self.dropout1 = nn.Dropout(p=0.3)

        self.fc2 = nn.Linear(64, 32)
        self.bn2 = nn.BatchNorm1d(32)
        self.dropout2 = nn.Dropout(p=0.2)

        self.fc3 = nn.Linear(32, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.dropout1(self.relu(self.bn1(self.fc1(x))))
        x = self.dropout2(self.relu(self.bn2(self.fc2(x))))
        return self.fc3(x)

def _prepare_ann_features(
    X_train: pd.DataFrame,
    X_val: pd.DataFrame,
    X_test: pd.DataFrame,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, StandardScaler]:
    """Encodes categorical columns and normalises features using train statistics."""
    X_train_enc = pd.get_dummies(X_train, columns=CAT_COLS, drop_first=True, dtype=float)
    X_val_enc = pd.get_dummies(X_val, columns=CAT_COLS, drop_first=True, dtype=float)
    X_test_enc = pd.get_dummies(X_test, columns=CAT_COLS, drop_first=True, dtype=float)

    # Ensure evaluation sets match training feature dimensions
    X_val_enc = X_val_enc.reindex(columns=X_train_enc.columns, fill_value=0.0)
    X_test_enc = X_test_enc.reindex(columns=X_train_enc.columns, fill_value=0.0)

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_enc)
    X_val_scaled = scaler.transform(X_val_enc)
    X_test_scaled = scaler.transform(X_test_enc)

    return X_train_scaled, X_val_scaled, X_test_scaled, scaler


def _train_ann(
    model: nn.Module,
    train_loader: DataLoader,
    pos_weight: torch.Tensor = None,
    epochs: int = 35,
    lr: float = 0.005,
) -> nn.Module:
    """Trains the network using BCE loss with optional positive class weighting."""
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for _ in range(epochs):
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
    return model


def _predict_probabilities(model: nn.Module, X_arr: np.ndarray) -> np.ndarray:
    """Returns predicted churn probabilities via sigmoid activation."""
    model.eval()
    dataset = ChurnDataset(X_arr, np.zeros(len(X_arr)))
    loader = DataLoader(dataset, batch_size=64, shuffle=False)
    probs_list = []

    with torch.no_grad():
        for batch_x, _ in loader:
            outputs = model(batch_x)
            probs = torch.sigmoid(outputs).cpu().numpy()
            probs_list.extend(probs)

    return np.array(probs_list).ravel()


def _sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_training_pipeline(
    raw_data_path: str = TRAIN_DATA_PATH,
    use_class_weight: bool = False,
    save_artifacts: bool = True,
) -> Dict[str, Any]:
    """Matches pipeline/training_pipeline.py interface with PyTorch ANN model."""
    configure_logging()

    # Step 1: Load and Validate (using existing Edu modules)
    raw = data_loader(raw_data_path)
    engineered, imputation_values = build_training_frame(raw)
    engineered = validate(engineered, STUDENT_INFO + FEATURES + [TARGET_FEATURE])

    # Step 2: Train / Val / Test Split (using existing Edu preprocess module)
    X, y = split_features_target(engineered)
    X_train, X_val, X_test, y_train, y_val, y_test = split_train_val_test(X, y)

    # Step 3: Categorical Alignment & Scaling for Neural Network
    X_tr_scaled, X_va_scaled, X_te_scaled, scaler = _prepare_ann_features(X_train, X_val, X_test)
    num_features = X_tr_scaled.shape[1]

    # Step 4: DataLoaders
    train_dataset = ChurnDataset(X_tr_scaled, y_train)
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)

    torch.manual_seed(42)
    np.random.seed(42)

    # Step 5: Class Weight Calculation
    pos_weight = None
    if use_class_weight:
        y_tr_arr = y_train.values if hasattr(y_train, "values") else y_train
        num_negative = float(np.sum(y_tr_arr == 0))
        num_positive = float(np.sum(y_tr_arr == 1))
        pos_weight = torch.tensor([num_negative / max(num_positive, 1.0)], dtype=torch.float32)

    # Step 6: Train PyTorch ChurnANN
    model = ChurnANN(input_dim=num_features)
    _train_ann(model, train_loader, pos_weight=pos_weight, epochs=35, lr=0.005)

# Step 7: Alert Threshold Selection on Val (Business Cost Minimisation)
    val_proba = _predict_probabilities(model, X_va_scaled)
    threshold_selection = select_threshold(
        y_val,
        val_proba,
        cost_false_alarm=DECISION_COST["false_alarm"],
        cost_missed_churn=DECISION_COST["missed_churn"],
    )
    chosen_threshold = threshold_selection["threshold"]

    # Step 8: Evaluate on Test Set
    test_proba = _predict_probabilities(model, X_te_scaled)
    metrics = evaluate_model(
        y_test,
        test_proba,
        threshold=chosen_threshold,
        k=PRECISION_AT_K,
        raw_churn_proba=test_proba,
    )
    error_analysis = false_negative_breakdown(
        X_test, y_test, test_proba, chosen_threshold, CAT_COLS
    )

    # Step 9: Assemble Meta (Mirrors training_pipeline.py schema)
    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_type": "PyTorch ANN (model_3)",
        "use_class_weight": use_class_weight,
        "data_file": str(raw_data_path),
        "data_rows": int(len(engineered)),
        "data_sha256": _sha256_of_file(raw_data_path),
        "features": FEATURES,
        "encoded_features_count": num_features,
        "cat_cols": CAT_COLS,
        "decision_cost": DECISION_COST,
        "chosen_threshold": chosen_threshold,
        "threshold_selection": threshold_selection,
        "imputation_values": imputation_values,
        "metrics": metrics,
        "error_analysis_false_negatives": error_analysis,
    }

    # Step 10: Artifact Persistence
    if save_artifacts:
        SAVED_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        metrics_dir = Path(METRICS_DIR)
        metrics_dir.mkdir(parents=True, exist_ok=True)

        suffix = "weighted" if use_class_weight else "unweighted"
        model_save_path = SAVED_MODELS_DIR / f"model_3_ann_{suffix}.pt"
        meta_save_path = metrics_dir / f"model_3_metrics_{suffix}.json"

        torch.save(model.state_dict(), model_save_path)
        with open(meta_save_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        logger.info("Saved model artifacts to %s and %s", model_save_path, meta_save_path)

    return {"model": model, "meta": meta}

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("RUNNING EDU MODEL 3: PYTORCH ANN PIPELINE")
    print("=" * 65)

    print("\n--- Training Model 3 (Version A: Without Class Weights) ---")
    res_a = run_training_pipeline(use_class_weight=False)

    print("\n--- Training Model 3 (Version B: With Class Weights) ---")
    res_b = run_training_pipeline(use_class_weight=True)

    report = pd.DataFrame([
        {
            "Variant": "Model 3 (Unweighted)",
            "Threshold": round(res_a["meta"]["chosen_threshold"], 2),
            "ROC-AUC": round(res_a["meta"]["metrics"]["roc_auc"], 4),
            "PR-AUC": round(res_a["meta"]["metrics"]["average_precision"], 4),
            "Precision": round(res_a["meta"]["metrics"]["precision"], 4),
            "Recall": round(res_a["meta"]["metrics"]["recall"], 4),
            "F1": round(res_a["meta"]["metrics"]["f1"], 4),
            f"P@{PRECISION_AT_K}": round(res_a["meta"]["metrics"].get("precision_at_k", float("nan")), 4),
        },
        {
            "Variant": "Model 3 (Weighted)",
            "Threshold": round(res_b["meta"]["chosen_threshold"], 2),
            "ROC-AUC": round(res_b["meta"]["metrics"]["roc_auc"], 4),
            "PR-AUC": round(res_b["meta"]["metrics"]["average_precision"], 4),
            "Precision": round(res_b["meta"]["metrics"]["precision"], 4),
            "Recall": round(res_b["meta"]["metrics"]["recall"], 4),
            "F1": round(res_b["meta"]["metrics"]["f1"], 4),
            f"P@{PRECISION_AT_K}": round(res_b["meta"]["metrics"].get("precision_at_k", float("nan")), 4),
        }
    ])

    print("\n" + "=" * 75)
    print("FINAL SPRINT EVALUATION REPORT (MODEL 3: ANN)")
    print("=" * 75)
    print(report.to_string(index=False))
    print("=" * 75 + "\n")

