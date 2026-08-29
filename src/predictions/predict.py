

def predict(model, X, customer_info, threshold=0.5):
    probs = model.predict_proba(X)[:, 1]
    result = customer_info.copy()
    result["churn_probability"] = probs
    result = result[result["churn_probability"] >= threshold]
    result = result.sort_values("churn_probability", ascending=False)
    return result
