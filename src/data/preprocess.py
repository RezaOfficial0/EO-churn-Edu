"""Turn a validated DataFrame into model-ready X / y and train/val/test splits."""
from sklearn.model_selection import train_test_split

from config import CAT_COLS, FEATURES, STUDENT_INFO, TARGET_FEATURE


def cast_categoricals(df):
    """CatBoost expects categorical columns as strings. Used by training and serving."""
    df = df.copy()
    for column in CAT_COLS:
        df[column] = df[column].astype(str)
    return df


def split_features_target(df):
    """Select exactly `config.FEATURES` as X and `config.TARGET_FEATURE` as y.

    Selecting FEATURES explicitly is what keeps a stray extra column in the
    customer's CSV from becoming an accidental model input.
    """
    X = cast_categoricals(df[FEATURES])
    y = df[TARGET_FEATURE].squeeze()
    return X, y


def split_train_val_test(X, y, *, test_size=0.2, val_size=0.2, random_state=42):
    """Stratified 3-way split: train / val / test.

    - train: fits the model
    - val:   early stopping, probability calibration, threshold selection
    - test:  evaluated once, for the reported numbers - never used to tune anything
    """
    X_trainval, X_test, y_trainval, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )
    # val_size is a fraction of the whole dataset; convert it to a fraction of
    # what is left after removing the test set.
    val_fraction_of_remainder = val_size / (1.0 - test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_trainval,
        y_trainval,
        test_size=val_fraction_of_remainder,
        random_state=random_state,
        stratify=y_trainval,
    )
    return X_train, X_val, X_test, y_train, y_val, y_test


def daily_process(df, id_columns=STUDENT_INFO):
    """Serving: split a daily batch into (passthrough id columns, model input X)."""
    customer_info = df[list(id_columns)].reset_index(drop=True)
    X = cast_categoricals(df[FEATURES]).reset_index(drop=True)
    return customer_info, X
