import pandas as pd
import pytest
import streamlit as st

# Disable @st.cache_data before any test module imports core/sources/pages.
# Without this, the decorator wraps function bodies at import time and
# coverage.py cannot attribute executed lines back to the original source.
st.cache_data = lambda f=None, **kw: f if f is not None else lambda fn: fn


def pytest_addoption(parser: pytest.Parser) -> None:
    # Opt-in flag for tests/evals/test_chat_evals.py's LLM-as-judge metrics
    # (FaithfulnessMetric, AnswerRelevancyMetric) — those cost judge tokens on
    # top of the live Gemini call the whole ``evals`` suite already needs, so
    # they only run with ``pytest tests/evals/ -m evals --judge``.
    parser.addoption(
        "--judge",
        action="store_true",
        default=False,
        help="Also run the LLM-as-judge metrics in tests/evals/test_chat_evals.py (costs judge tokens).",
    )


@pytest.fixture
def judge_enabled(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--judge"))


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
def make_ratings():
    defaults = {"user_rating": 3.0, "genres": "Drama", "directors": "Alice", "release_year": 2000}

    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame([{**defaults, **r} for r in rows])

    return _factory


@pytest.fixture
def make_events_df():
    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    return _factory
