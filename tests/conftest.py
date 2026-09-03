"""Shared test fixtures.

`sys.path` is set so `import config`, `import src...`, `import api...` work when
pytest is run from the repo root.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config import DAILY_DATA_PATH, RAW_DATA_PATH  # noqa: E402


@pytest.fixture(scope="session")
def raw_df() -> pd.DataFrame:
    """The full raw training export (has nulls)."""
    return pd.read_csv(RAW_DATA_PATH)


@pytest.fixture(scope="session")
def daily_df() -> pd.DataFrame:
    """The raw daily serving sample (has nulls, no missing-flag columns)."""
    return pd.read_csv(DAILY_DATA_PATH)
