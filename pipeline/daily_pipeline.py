import pandas as pd

from config import STUDENT_INFO, FEATURES, SHAP_TOP_N_FEATURES
from src.model.load import load_model
from src.data.loader import data_loader
from src.data.Validation import validate
from src.data.preprocess import daily_process
from src.predictions.predict import predict
from src.explainer.shap_explainer import create_explainer, explain_customers


def row_to_native(row):
    """pandas/numpy skalerlerini JSON'a cevrilebilir Python tiplerine indirir."""
    out = {}
    for key, value in row.items():
        if pd.isna(value):
            out[key] = None
        elif hasattr(value, "item"):
            out[key] = value.item()
        else:
            out[key] = value
    return out


def run_daily_pipeline(daily_data_path, model_path, threshold=0.5, top_n=SHAP_TOP_N_FEATURES):
    model = load_model(model_path)
    data = data_loader(daily_data_path)
    validate(data, STUDENT_INFO + FEATURES)

    customer_info, X = daily_process(data, STUDENT_INFO)
    result = predict(model, X, customer_info, threshold=threshold)

    explainer = create_explainer(model)
    explanations = explain_customers(explainer, X, top_n=top_n)
    result["top_reasons"] = [
        ", ".join(f"{r['feature']} ({r['impact']:+.2f})" for r in explanations[i])
        for i in result.index
    ]

    result["top_reasons_detail"] = [explanations[i] for i in result.index]

    # Frontend'in ogrenci detayinda gosterebilmesi icin ham feature degerleri.
    result["features"] = [row_to_native(X.loc[i]) for i in result.index]

    return result.reset_index(drop=True)
