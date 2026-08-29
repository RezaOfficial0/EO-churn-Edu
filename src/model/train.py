

def train(model, X_train,X_test,y_train,y_test):
    history = model.fit(
              X_train,
              y_train,
              eval_set=(X_test, y_test),
              early_stopping_rounds=50
            )

    return history
