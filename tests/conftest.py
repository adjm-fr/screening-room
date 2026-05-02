import pandas as pd
import pytest


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
