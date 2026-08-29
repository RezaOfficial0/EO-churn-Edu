from sklearn.model_selection import train_test_split

def target_split(df ,STUDENT_INFO , TARGET_FEATURE , CAT_COLS):
    X = df.drop(columns=STUDENT_INFO)
    X_last = X.drop(columns=TARGET_FEATURE)
    cat_features = [X_last.columns.get_loc(c) for c in CAT_COLS]
    y = df[TARGET_FEATURE].squeeze()

    return X_last ,y , cat_features


def train_test_splits(X_last ,y , cat_features):
    X_train,X_test,y_train,y_test = train_test_split(
        X_last,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y
    )
    return X_train , X_test , y_train , y_test, cat_features


def preprocess(df,STUDENT_INFO , TARGET_FEATURE , CAT_COLS):
    X_last , y , cat_features = target_split(df,STUDENT_INFO , TARGET_FEATURE , CAT_COLS)
    X_train, X_test, y_train, y_test, cat_features = train_test_splits(X_last , y , cat_features)

    for col in CAT_COLS:
        X_train[col] = X_train[col].astype(str)
        X_test[col] = X_test[col].astype(str)

    return X_train , X_test , y_train , y_test, cat_features


def daily_process(df , id_columns):
    customer_info = df[list(id_columns)]
    x_daily = df.drop(columns=id_columns)
    return customer_info , x_daily