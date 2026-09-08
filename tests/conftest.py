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
from src.data import loader  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_backend(monkeypatch):
    """Pin every test to the CSV backend, whatever the developer's .env says.

    Without this, running `pytest` in a shell configured for DATA_SOURCE=db sends
    the CSV tests at a real database: they read rows they did not write, and
    `append_to_alert_log` tries to INSERT into the developer's actual `alerts`
    table. A test suite must not change behaviour with the environment, and must
    never touch a database that was not created for it.

    DATABASE_URL is blanked as well, so a test that somehow still reaches for a
    database fails loudly with "DATABASE_URL is not set" rather than quietly
    writing somewhere real. tests/test_db_integration.py points both back at
    TEST_DATABASE_URL for its own tests.
    """
    monkeypatch.setattr(loader, "DATA_SOURCE", "csv")
    monkeypatch.setattr(loader, "DATABASE_URL", None)
    monkeypatch.setattr(loader, "_engine", None)


@pytest.fixture(scope="session")
def raw_df() -> pd.DataFrame:
    """The full raw training export (has nulls)."""
    return pd.read_csv(RAW_DATA_PATH)


@pytest.fixture(scope="session")
def daily_df() -> pd.DataFrame:
    """The raw daily serving sample (has nulls, no missing-flag columns)."""
    return pd.read_csv(DAILY_DATA_PATH)
