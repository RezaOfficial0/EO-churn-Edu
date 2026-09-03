import shap


def create_explainer(model):
    return shap.TreeExplainer(model)


def explain_customers(explainer, customer_data, top_n):
    """Return, for each row, the `top_n` features with the largest SHAP impact.

    Each item is a list of {"feature": name, "impact": signed_value}, sorted by
    absolute impact. Positive impact pushes churn risk up, negative pushes it down.
    """
    if len(customer_data) == 0:
        return []

    shap_values = explainer.shap_values(customer_data)
    if isinstance(shap_values, list):  # older SHAP returns [class0, class1]
        shap_values = shap_values[1]

    feature_names = list(customer_data.columns)
    explanations = []
    for row_index in range(len(customer_data)):
        impacts = [
            {"feature": feature, "impact": float(value)}
            for feature, value in zip(feature_names, shap_values[row_index])
        ]
        impacts.sort(key=lambda item: abs(item["impact"]), reverse=True)
        explanations.append(impacts[:top_n])
    return explanations
