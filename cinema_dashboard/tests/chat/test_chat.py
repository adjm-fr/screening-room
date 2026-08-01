"""Tests for chat.ui — context builders, disk persistence and tool dispatch.

The streaming-availability builder and the ``save_chat_state`` /
``load_chat_state`` / ``delete_chat_state`` helpers are covered here (all pure,
no Streamlit runtime needed). ``_ask_gemini``'s tool-dispatch loop is covered
with the Gemini client and the Streamlit module both mocked out — no network,
no session. ``render_chat`` still needs a real Streamlit session and is covered
by manual verification.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import pytest
from chat import _streaming_context, load_chat_state
from chat.ui import (
    ChatContext,
    ChatState,
    _ask_gemini,
    delete_chat_state,
    save_chat_state,
)
from google.genai import types


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
    with caplog.at_level(logging.WARNING, logger="chat.ui"):
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


# ── tool dispatch ────────────────────────────────────────────────────────────


def _chunk(part: types.Part) -> types.GenerateContentResponse:
    """Wrap one part in the response shape ``generate_content_stream`` yields."""
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])


def _call_chunk(name: str, args: dict) -> types.GenerateContentResponse:
    return _chunk(types.Part(function_call=types.FunctionCall(name=name, args=args)))


def _text_chunk(text: str) -> types.GenerateContentResponse:
    return _chunk(types.Part(text=text))


@pytest.fixture
def ctx():
    wl = pd.DataFrame(
        [
            {
                "letterboxd_title": "Ran",
                "french_title": "Ran",
                "genres": "Drama",
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2026-07-25 20:00"),
                "match": 91.0,
            }
        ]
    )
    return ChatContext(
        taste="Average rating given: 2.5/5 across 10 films",
        showtimes_md="| Ran |",
        streaming_md="",
        known_theaters=["Le Champo"],
        theaters_csv=None,
        wl_shows=wl,
        wl_scored=wl,
        n_movies=1,
        n_screenings=1,
    )


@pytest.fixture
def fake_gemini(mocker):
    """Mock the Gemini client and Streamlit; return the client whose stream you script."""
    mocker.patch("chat.ui.st")  # the tool expanders need no real session here
    client = mocker.MagicMock()
    mocker.patch("chat.ui.genai.Client", return_value=client)
    return client


@pytest.mark.parametrize(
    ("tool_name", "args", "handler", "expected_kwargs"),
    [
        ("top_matches", {"n": 3, "genre": "Drama"}, "top_matches", {"n": 3, "genre": "Drama"}),
        (
            "showtimes_query",
            {"title": "Ran", "theater": "Champo", "day": "2026-07-25"},
            "showtimes_query",
            {"title": "Ran", "theater": "Champo", "day": "2026-07-25"},
        ),
    ],
)
def test_ask_gemini_dispatches_new_tools(mocker, ctx, fake_gemini, tool_name, args, handler, expected_kwargs):
    spy = mocker.patch(f"chat.ui.{handler}", return_value=[{"title": "Ran"}])
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk(tool_name, args)]),
        iter([_text_chunk("Ran, 20:00 at Le Champo.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Ran, 20:00 at Le Champo."

    spy.assert_called_once()
    frame, kwargs = spy.call_args.args[0], spy.call_args.kwargs
    assert frame is ctx.wl_scored  # handlers query the scored frame, nothing else
    assert kwargs == expected_kwargs
    assert pending_ref[0] is None  # only search_theater feeds the "add theater?" flow


def test_ask_gemini_search_theater_still_populates_pending(mocker, ctx, fake_gemini):
    theaters = [{"id": "T9", "name": "Le Brady"}]
    mocker.patch("chat.ui.search_theaters", return_value=theaters)
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk("search_theater", {"query": "Brady"})]),
        iter([_text_chunk("Found Le Brady.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Found Le Brady."
    assert pending_ref[0] == theaters


def test_ask_gemini_chains_two_tool_rounds(mocker, ctx, fake_gemini):
    top = mocker.patch("chat.ui.top_matches", return_value=[{"title": "Ran"}])
    shows = mocker.patch("chat.ui.showtimes_query", return_value=[{"title": "Ran"}])
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk("top_matches", {"n": 1})]),
        iter([_call_chunk("showtimes_query", {"title": "Ran"})]),
        iter([_text_chunk("Ran at 20:00.")]),
    ]

    stream, _ = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Ran at 20:00."
    top.assert_called_once()
    shows.assert_called_once()


def test_ask_gemini_stops_after_the_round_budget(mocker, ctx, fake_gemini):
    spy = mocker.patch("chat.ui.top_matches", return_value=[])
    # The model asks for a tool on every round; only MAX_TOOL_ROUNDS are served.
    fake_gemini.models.generate_content_stream.side_effect = lambda **_: iter([_call_chunk("top_matches", {})])

    stream, _ = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == ""
    assert spy.call_count == 2
    assert fake_gemini.models.generate_content_stream.call_count == 3


def test_ask_gemini_ignores_an_unknown_tool_name(mocker, ctx, fake_gemini, caplog):
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk("book_ticket", {"seat": "J12"})]),
        iter([_text_chunk("I can't book seats.")]),
    ]

    with caplog.at_level(logging.WARNING, logger="chat.ui"):
        stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
        assert "".join(stream) == "I can't book seats."
    assert "unknown tool call" in caplog.text.lower()
    assert pending_ref[0] is None
