"""Tests for chat.state — the ChatState dataclass and its data/chat_state.json layer."""

from __future__ import annotations

import json
import logging

import pandas as pd

from chat.state import ChatState, delete_chat_state, load_chat_state, save_chat_state


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
    # `chat.state`, not `chat.ui`: this asserted the wrong logger name for as long
    # as it has existed and passed anyway, because caplog captures through root
    # propagation — so it was not actually pinning where the warning comes from.
    with caplog.at_level(logging.WARNING, logger="chat.state"):
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
