"""Tests for utils.chat — context builders.

Only the streaming-availability builder is covered here. The HF API call
``_ask_hf`` and ``render_chat`` need a Streamlit session and live API and are
covered by manual verification, not unit tests.
"""

from __future__ import annotations

import pandas as pd
from utils.chat import _streaming_context


def test_streaming_context_empty_when_no_columns():
    df = pd.DataFrame({"letterboxd_title": ["A"]})
    assert _streaming_context(df) == ""


def test_streaming_context_skips_films_without_providers():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["No Streaming", "Has Streaming"],
            "flatrate": [[], ["mubi"]],
        }
    )
    out = _streaming_context(df)
    assert "Has Streaming" in out
    assert "mubi" in out
    assert "No Streaming" not in out


def test_streaming_context_dedups_by_title():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Same", "Same"],
            "flatrate": [["mubi"], ["mubi"]],
        }
    )
    assert _streaming_context(df).count("Same") == 1
