import os
import pandas as pd

# Import csv as DataFrame
def data_loader(PATH):
    if not os.path.exists(PATH):
        raise FileNotFoundError(f"Data file not found: {PATH}")
    return pd.read_csv(PATH)
