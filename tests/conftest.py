import pandas as pd
import pytest
import streamlit as st

# Disable @st.cache_data before any test module imports utils/pages.
# Without this, the decorator wraps function bodies at import time and
# coverage.py cannot attribute executed lines back to the original source.
st.cache_data = lambda f=None, **kw: f if f is not None else lambda fn: fn


@pytest.fixture
def make_showtimes():
    defaults = {"theater_id": "T1", "theater_name": "Cinema"}

    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame([{**defaults, **r} for r in rows])

    return _factory


@pytest.fixture
def make_watchlist():
    defaults = {"slug": "test-slug", "runtime": 100, "genres": "Drama"}

    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame([{**defaults, **r} for r in rows])

    return _factory


@pytest.fixture
def make_events_df():
    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    return _factory
