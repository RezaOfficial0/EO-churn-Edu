def train(model, X_train, y_train, X_val, y_val, early_stopping_rounds=50):
    """Fit the model, using the validation set (never the test set) for early stopping."""
    model.fit(
        X_train,
        y_train,
        eval_set=(X_val, y_val),
        early_stopping_rounds=early_stopping_rounds,
    )
    return model
