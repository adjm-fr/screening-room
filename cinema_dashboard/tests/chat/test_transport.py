"""Tests for chat.transport — the bounded tool-dispatch loop around Gemini.

The Gemini client and the Streamlit module are both mocked out, so no network and
no session. The load-bearing case is the parallel one: a single model turn can
carry several function_call parts, and Gemini rejects a turn whose responses do
not cover its calls one-for-one.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from google.genai import types

from chat.prompt import ChatContext
from chat.transport import _ask_gemini


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
    streaming = pd.DataFrame(
        [{"letterboxd_title": "Ran", "french_title": "Ran", "flatrate": ["mubi"], "free": [], "match": 91.0}]
    )
    return ChatContext(
        taste="Average rating given: 2.5/5 across 10 films",
        showtimes_md="| Ran |",
        streaming_md="- Ran — flatrate=mubi",
        known_theaters=["Le Champo"],
        theaters_csv=None,
        wl_shows=wl,
        wl_scored=wl,
        streaming_df=streaming,
        slug_by_title={"Ran": [("ran", "Akira Kurosawa")]},
        n_movies=1,
        n_screenings=1,
    )


@pytest.fixture
def fake_gemini(mocker):
    """Mock the Gemini client and Streamlit; return the client whose stream you script."""
    mocker.patch("chat.transport.st")  # the tool expanders need no real session here
    client = mocker.MagicMock()
    mocker.patch("chat.transport.genai.Client", return_value=client)
    return client


@pytest.mark.parametrize(
    ("tool_name", "args", "handler", "expected_kwargs", "expected_frame_attr"),
    [
        ("top_matches", {"n": 3, "genre": "Drama"}, "top_matches", {"n": 3, "genre": "Drama"}, "wl_scored"),
        (
            "showtimes_query",
            {"title": "Ran", "theater": "Champo", "day": "2026-07-25"},
            "showtimes_query",
            {"title": "Ran", "theater": "Champo", "day": "2026-07-25"},
            "wl_scored",
        ),
        (
            "streaming_query",
            {"title": "Ran", "provider": "mubi"},
            "streaming_query",
            {"title": "Ran", "provider": "mubi"},
            "streaming_df",
        ),
    ],
)
def test_ask_gemini_dispatches_new_tools(
    mocker, ctx, fake_gemini, tool_name, args, handler, expected_kwargs, expected_frame_attr
):
    spy = mocker.patch(f"chat.transport.{handler}", return_value=[{"title": "Ran"}])
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk(tool_name, args)]),
        iter([_text_chunk("Ran, 20:00 at Le Champo.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Ran, 20:00 at Le Champo."

    spy.assert_called_once()
    frame, kwargs = spy.call_args.args[0], spy.call_args.kwargs
    assert frame is getattr(ctx, expected_frame_attr)  # handlers query the matching pre-built frame, nothing else
    assert kwargs == expected_kwargs
    assert pending_ref[0] is None  # only search_theater feeds the "add theater?" flow


def test_ask_gemini_search_theater_still_populates_pending(mocker, ctx, fake_gemini):
    theaters = [{"id": "T9", "name": "Le Brady"}]
    mocker.patch("chat.transport.search_theaters", return_value=theaters)
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk("search_theater", {"query": "Brady"})]),
        iter([_text_chunk("Found Le Brady.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Found Le Brady."
    assert pending_ref[0] == theaters


def test_ask_gemini_chains_two_tool_rounds(mocker, ctx, fake_gemini):
    top = mocker.patch("chat.transport.top_matches", return_value=[{"title": "Ran"}])
    shows = mocker.patch("chat.transport.showtimes_query", return_value=[{"title": "Ran"}])
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
    spy = mocker.patch("chat.transport.top_matches", return_value=[])
    # The model asks for a tool on every round; only MAX_TOOL_ROUNDS are served.
    fake_gemini.models.generate_content_stream.side_effect = lambda **_: iter([_call_chunk("top_matches", {})])

    stream, _ = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == ""
    assert spy.call_count == 2
    assert fake_gemini.models.generate_content_stream.call_count == 3


def _parallel_call_chunk(*calls: tuple[str, dict]) -> types.GenerateContentResponse:
    """One model turn carrying several function calls, as a multi-theater question produces."""
    parts = [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


def test_ask_gemini_runs_every_parallel_search_theater_call(mocker, ctx, fake_gemini):
    results = {"Brady": [{"id": "T1", "name": "Le Brady"}], "Champo": [{"id": "T2", "name": "Le Champo"}]}
    spy = mocker.patch("chat.transport.search_theaters", side_effect=lambda q: results[q])
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_parallel_call_chunk(("search_theater", {"query": "Brady"}), ("search_theater", {"query": "Champo"}))]),
        iter([_text_chunk("Both are Paris cinemas.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "do you know the Brady and the Champo?"}])
    assert "".join(stream) == "Both are Paris cinemas."

    assert [c.args[0] for c in spy.call_args_list] == ["Brady", "Champo"]
    # Both theaters reach the "add this theater?" flow, not just the last call's.
    assert pending_ref[0] == [{"id": "T1", "name": "Le Brady"}, {"id": "T2", "name": "Le Champo"}]

    # Gemini rejects a turn whose function responses don't cover its calls one-for-one.
    follow_up = fake_gemini.models.generate_content_stream.call_args_list[1].kwargs["contents"]
    assert [p.function_response.name for p in follow_up[-1].parts] == ["search_theater", "search_theater"]


def test_ask_gemini_dedupes_theaters_across_parallel_calls(mocker, ctx, fake_gemini):
    """Two queries hitting the same cinema must not key two Add buttons on one id."""
    mocker.patch("chat.transport.search_theaters", return_value=[{"id": "T1", "name": "Le Brady"}])
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_parallel_call_chunk(("search_theater", {"query": "Brady"}), ("search_theater", {"query": "Le Brady"}))]),
        iter([_text_chunk("Same cinema.")]),
    ]

    stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
    assert "".join(stream) == "Same cinema."
    assert pending_ref[0] == [{"id": "T1", "name": "Le Brady"}]


def test_ask_gemini_ignores_an_unknown_tool_name(mocker, ctx, fake_gemini, caplog):
    fake_gemini.models.generate_content_stream.side_effect = [
        iter([_call_chunk("book_ticket", {"seat": "J12"})]),
        iter([_text_chunk("I can't book seats.")]),
    ]

    with caplog.at_level(logging.WARNING, logger="chat.transport"):
        stream, pending_ref = _ask_gemini(ctx, [{"role": "user", "content": "?"}])
        assert "".join(stream) == "I can't book seats."
    assert "unknown tool call" in caplog.text.lower()
    assert pending_ref[0] is None
