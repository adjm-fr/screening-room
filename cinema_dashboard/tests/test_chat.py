"""Tests for utils.chat — context builders and disk persistence.

The streaming-availability builder and the ``save_chat_state`` /
``load_chat_state`` / ``delete_chat_state`` helpers are covered here (all pure,
no Streamlit runtime needed). The Gemini API call ``_ask_gemini`` and
``render_chat`` need a Streamlit session and live API and are covered by
manual verification, not unit tests.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
from utils.chat import ChatState, _streaming_context, delete_chat_state, load_chat_state, save_chat_state


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


def test_streaming_context_appends_free_segment_when_present():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Has Free"],
            "flatrate": [["mubi"]],
            "free": [["arte"]],
        }
    )
    out = _streaming_context(df)
    assert "flatrate=mubi" in out
    assert "; free=arte" in out


def test_streaming_context_omits_free_segment_when_absent():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["No Free"],
            "flatrate": [["mubi"]],
            "free": [[]],
        }
    )
    out = _streaming_context(df)
    assert "flatrate=mubi" in out
    assert "free=" not in out


def test_streaming_context_includes_free_only_film():
    # A film with no flatrate provider but a free one must still surface —
    # free platforms are available to everyone.
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Free Only"],
            "flatrate": [[]],
            "free": [["arte"]],
        }
    )
    out = _streaming_context(df)
    assert "Free Only" in out
    assert "; free=arte" in out


def test_chat_state_round_trip(tmp_path):
    path = tmp_path / "chat_state.json"
    state = ChatState(
        messages=[{"role": "user", "content": "what's on tonight?"}, {"role": "assistant", "content": "Ran at 20:00."}],
        pinned_recs=[{"letterboxd_title": "Ran", "showtimes": pd.Timestamp("2026-07-15 20:00"), "theater_name": "Cinema"}],
    )
    save_chat_state(state, path)
    loaded = load_chat_state(path)
    assert loaded.messages == state.messages
    assert len(loaded.pinned_recs) == 1
    pin = loaded.pinned_recs[0]
    assert pin["letterboxd_title"] == "Ran"
    assert pin["theater_name"] == "Cinema"
    # Timestamps go through ``default=str``; the pinned renderer re-parses them.
    assert pd.to_datetime(pin["showtimes"]) == state.pinned_recs[0]["showtimes"]


def test_save_chat_state_persists_transcript_and_pins_only(tmp_path):
    path = tmp_path / "chat_state.json"
    state = ChatState(
        messages=[{"role": "user", "content": "hi"}],
        pending_theaters=[{"id": "T1", "name": "Brady"}],
        pinnable=["Ran"],
        last_chip="chip",
    )
    save_chat_state(state, path)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"messages", "pinned_recs"}


def test_load_chat_state_absent_file_returns_fresh_state(tmp_path):
    assert load_chat_state(tmp_path / "missing.json") == ChatState()


def test_load_chat_state_corrupt_file_returns_fresh_state(tmp_path, caplog):
    path = tmp_path / "chat_state.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="utils.chat"):
        loaded = load_chat_state(path)
    assert loaded == ChatState()
    assert "unreadable chat state" in caplog.text


def test_load_chat_state_wrong_shape_returns_fresh_state(tmp_path):
    path = tmp_path / "chat_state.json"
    path.write_text(json.dumps({"messages": "not a list", "pinned_recs": {}}), encoding="utf-8")
    assert load_chat_state(path) == ChatState()


def test_delete_chat_state_removes_file_and_tolerates_missing(tmp_path):
    path = tmp_path / "chat_state.json"
    save_chat_state(ChatState(), path)
    assert path.exists()
    delete_chat_state(path)
    assert not path.exists()
    delete_chat_state(path)  # second delete must not raise
