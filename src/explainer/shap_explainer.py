import shap

def create_explainer(model):
    return shap.TreeExplainer(model)


def explain_customers(explainer, customer_data, top_n):
    shap_values = explainer.shap_values(customer_data)

    if isinstance(shap_values, list):
        shap_values = shap_values[1]

    feature_names = list(customer_data.columns)
    explanations = []

    for i in range(len(customer_data)):
        customer_shap = shap_values[i]

        feature_impacts = [
            {"feature": feature, "impact": float(value)}
            for feature, value in zip(feature_names, customer_shap)
        ]

        feature_impacts.sort(key=lambda x: abs(x["impact"]), reverse=True)
        explanations.append(feature_impacts[:top_n])

    return explanations