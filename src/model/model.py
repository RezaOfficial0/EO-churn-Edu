from catboost import CatBoostClassifier

def build_model(iterations , depth , learning_rate , cat_features):
    model = CatBoostClassifier(
        iterations=iterations,
        depth=depth,
        learning_rate=learning_rate,
        cat_features=cat_features,
        auto_class_weights="Balanced",
        eval_metric="AUC",
        random_state=42,
        verbose=False,
    )
    return model

