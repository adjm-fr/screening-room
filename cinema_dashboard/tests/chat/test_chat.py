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
from google.genai import types

from chat.prompt import STREAMING_CONTEXT_TOP_N, _slug_by_title, _streaming_context
from chat.state import load_chat_state
from chat.ui import (
    ChatContext,
    ChatState,
    _ask_gemini,
    _pin_caption_html,
    delete_chat_state,
    resolve_pin,
    save_chat_state,
)


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


# ── _streaming_context: top-N narrowing ─────────────────────────────────────


def _streaming_frame(n: int, *, scored: bool) -> pd.DataFrame:
    """``n`` distinct streaming films, descending match when ``scored``."""
    rows = [
        {
            "letterboxd_title": f"Film {i:03d}",
            "flatrate": ["mubi"],
            "free": [],
            **({"match": float(n - i)} if scored else {}),
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


def test_streaming_context_falls_back_to_full_list_without_a_match_column():
    # No scores available (build_chat_context's fallback path) — narrowing
    # would need a ranking it doesn't have, so the block stays uncapped.
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=False)
    out = _streaming_context(df)
    assert out.count("Film ") == STREAMING_CONTEXT_TOP_N + 25
    assert "streaming_query" not in out  # nothing was left out, so no marker


def test_streaming_context_caps_to_top_n_when_scored():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)
    out = _streaming_context(df)
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(lines) == STREAMING_CONTEXT_TOP_N


def test_streaming_context_keeps_the_highest_match_films_when_capped():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)  # match descends as i increases
    out = _streaming_context(df)
    assert "Film 000" in out  # highest match (n - 0)
    assert f"Film {STREAMING_CONTEXT_TOP_N + 24:03d}" not in out  # lowest match, narrowed out


def test_streaming_context_appends_marker_line_when_truncated():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)
    out = _streaming_context(df)
    assert "streaming_query" in out
    assert "+25 more" in out


def test_streaming_context_no_marker_line_when_under_the_cap():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N - 5, scored=True)
    out = _streaming_context(df)
    assert "streaming_query" not in out
    assert out.count("- Film ") == STREAMING_CONTEXT_TOP_N - 5


def test_streaming_context_marker_line_is_not_in_the_pinned_line_format():
    # The eval goldens pin "- {title} — flatrate=...": the marker must never
    # be mistaken for a film entry by a model or a test parsing that shape.
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 1, scored=True)
    out = _streaming_context(df)
    marker = next(line for line in out.splitlines() if "streaming_query" in line)
    assert not marker.startswith("- ")


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


# ── pinned recommendations ───────────────────────────────────────────────────


def test_slug_by_title_keys_both_title_spellings():
    """A pin's stored title may be either spelling, depending on what the join matched."""
    wl = pd.DataFrame([{"title": "Fail Safe", "french_title": "Point limite", "slug": "fail-safe", "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {
        "Fail Safe": [("fail-safe", "Sidney Lumet")],
        "Point limite": [("fail-safe", "Sidney Lumet")],
    }


def test_slug_by_title_keeps_every_film_sharing_a_title():
    """Remakes collide; last-write-wins would silently point a pin at the wrong film."""
    wl = pd.DataFrame(
        [
            {"title": "King Lear", "french_title": "Le Roi Lear", "slug": "king-lear", "directors": "Peter Brook"},
            {"title": "King Lear", "french_title": "Le Roi Lear", "slug": "king-lear-1987", "directors": "Jean-Luc Godard"},
        ]
    )

    assert _slug_by_title(wl)["King Lear"] == [("king-lear", "Peter Brook"), ("king-lear-1987", "Jean-Luc Godard")]


def test_slug_by_title_does_not_duplicate_a_film_across_title_spellings():
    """title == french_title must not list the same slug twice."""
    wl = pd.DataFrame([{"title": "Güeros", "french_title": "Güeros", "slug": "gueros", "directors": "Alonso Ruizpalacios"}])

    assert _slug_by_title(wl)["Güeros"] == [("gueros", "Alonso Ruizpalacios")]


def test_slug_by_title_skips_rows_without_a_slug():
    wl = pd.DataFrame([{"title": "Fail Safe", "french_title": "Point limite", "slug": None, "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {}


def test_slug_by_title_without_a_slug_column_is_empty():
    assert _slug_by_title(pd.DataFrame([{"title": "Fail Safe"}])) == {}


def test_slug_by_title_tolerates_a_missing_french_title_column():
    wl = pd.DataFrame([{"title": "Fail Safe", "slug": "fail-safe", "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {"Fail Safe": [("fail-safe", "Sidney Lumet")]}


def test_slug_by_title_tolerates_a_missing_directors_column():
    wl = pd.DataFrame([{"title": "Fail Safe", "slug": "fail-safe"}])

    assert _slug_by_title(wl) == {"Fail Safe": [("fail-safe", "")]}


@pytest.fixture
def pin_shows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "letterboxd_title": "Ran",
                "letterboxd_slug": "ran",
                "showtimes": pd.Timestamp("2026-08-06 21:00"),
                "theater_name": "Le Champo",
            },
            {
                "letterboxd_title": "Ran",
                "letterboxd_slug": "ran",
                "showtimes": pd.Timestamp("2026-08-04 18:00"),
                "theater_name": "MK2 Beaubourg",
            },
        ]
    )


def test_resolve_pin_relinks_a_snapshot_saved_without_a_slug(pin_shows):
    """The #42 case: pins saved before the slug was carried through the join."""
    stored = {"letterboxd_title": "Ran", "showtimes": "2026-07-01 20:00", "theater_name": "Old"}

    assert resolve_pin(stored, pin_shows)["letterboxd_slug"] == "ran"


def test_resolve_pin_prefers_the_slug_over_the_title(pin_shows):
    stored = {"letterboxd_slug": "ran", "letterboxd_title": "a stale retitle", "showtimes": "2026-07-01 20:00"}

    assert resolve_pin(stored, pin_shows)["theater_name"] == "MK2 Beaubourg"


def test_resolve_pin_returns_the_next_upcoming_screening(pin_shows):
    """A pin points at a film; the frozen showtime would go stale within a week."""
    resolved = resolve_pin({"letterboxd_title": "Ran"}, pin_shows)

    assert resolved["showtimes"] == pd.Timestamp("2026-08-04 18:00")


def test_resolve_pin_falls_back_when_the_film_no_longer_screens(pin_shows):
    stored = {"letterboxd_title": "Solaris", "theater_name": "Le Champo"}

    assert resolve_pin(stored, pin_shows) == stored


def test_resolve_pin_falls_back_on_an_empty_join():
    stored = {"letterboxd_title": "Ran"}

    assert resolve_pin(stored, pd.DataFrame()) == stored


def test_resolve_pin_relinks_a_film_whose_screenings_have_passed(pin_shows):
    """The film drops out of wl_shows, but its detail page (cache-backed) is still there."""
    stored = {"letterboxd_title": "Solaris", "theater_name": "Le Champo"}

    resolved = resolve_pin(stored, pin_shows, {"Solaris": [("solaris", "Andrei Tarkovsky")]})

    assert resolved["letterboxd_slug"] == "solaris"
    assert resolved["theater_name"] == "Le Champo"  # the rest of the snapshot is untouched


def test_resolve_pin_relinks_off_an_empty_join():
    resolved = resolve_pin({"letterboxd_title": "Solaris"}, pd.DataFrame(), {"Solaris": [("solaris", "Andrei Tarkovsky")]})

    assert resolved["letterboxd_slug"] == "solaris"


def test_resolve_pin_leaves_an_unknown_title_alone(pin_shows):
    stored = {"letterboxd_title": "Not On The Watchlist"}

    assert resolve_pin(stored, pin_shows, {"Solaris": [("solaris", "Andrei Tarkovsky")]}) == stored


def test_resolve_pin_does_not_overwrite_a_slug_it_already_has(pin_shows):
    """A pin that already carries a slug needs no recovery — never guess over it."""
    stored = {"letterboxd_slug": "solaris-1972", "letterboxd_title": "Solaris"}

    assert resolve_pin(stored, pin_shows, {"Solaris": [("solaris", "Andrei Tarkovsky")]})["letterboxd_slug"] == "solaris-1972"


def test_resolve_pin_tolerates_a_join_without_the_slug_column():
    shows = pd.DataFrame([{"letterboxd_title": "Ran", "theater_name": "Le Champo"}])

    assert resolve_pin({"letterboxd_slug": "ran", "letterboxd_title": "Ran"}, shows)["theater_name"] == "Le Champo"


# ── pins vs. remakes: a title does not identify a film ──────────────────────

#: Two different films, same title — the real watchlist has 22 such collisions.
_LEARS = [("king-lear", "Peter Brook"), ("king-lear-1987", "Jean-Luc Godard")]


@pytest.fixture
def lear_shows() -> pd.DataFrame:
    """Both King Lears screening, Brook's first — so .iloc[0] alone would pick him."""
    return pd.DataFrame(
        [
            {
                "letterboxd_title": "King Lear",
                "letterboxd_slug": "king-lear",
                "directors": "Peter Brook",
                "showtimes": pd.Timestamp("2026-08-04 18:00"),
            },
            {
                "letterboxd_title": "King Lear",
                "letterboxd_slug": "king-lear-1987",
                "directors": "Jean-Luc Godard",
                "showtimes": pd.Timestamp("2026-08-06 21:00"),
            },
        ]
    )


def test_resolve_pin_picks_the_right_remake_by_director(lear_shows):
    """Godard's pin must not resolve to Brook's film just because his screens first."""
    stored = {"letterboxd_title": "King Lear", "directors": "Jean-Luc Godard"}

    assert resolve_pin(stored, lear_shows)["letterboxd_slug"] == "king-lear-1987"


def test_resolve_pin_refuses_an_unconfirmable_remake(lear_shows):
    """No director on the pin — linking to either film would be a coin flip."""
    stored = {"letterboxd_title": "King Lear"}

    assert resolve_pin(stored, lear_shows) == stored


def test_resolve_pin_recovers_the_right_remake_slug_by_director():
    stored = {"letterboxd_title": "King Lear", "directors": "Jean-Luc Godard"}

    assert resolve_pin(stored, pd.DataFrame(), {"King Lear": _LEARS})["letterboxd_slug"] == "king-lear-1987"


def test_resolve_pin_recovers_no_slug_for_an_unconfirmable_remake():
    """Silently opening the wrong film is a worse failure than an unlinked pin."""
    stored = {"letterboxd_title": "King Lear"}

    assert resolve_pin(stored, pd.DataFrame(), {"King Lear": _LEARS}) == stored


def test_resolve_pin_confirms_a_remake_across_director_name_drift():
    """Confirmation is token containment, matching the showtimes join's tolerance."""
    stored = {"letterboxd_title": "King Lear", "directors": "Godard"}

    assert resolve_pin(stored, pd.DataFrame(), {"King Lear": _LEARS})["letterboxd_slug"] == "king-lear-1987"


def test_resolve_pin_recovers_an_unambiguous_title_without_a_director():
    """One candidate needs no confirmation — the title already identifies the film."""
    stored = {"letterboxd_title": "Ran"}

    assert resolve_pin(stored, pd.DataFrame(), {"Ran": [("ran", "Akira Kurosawa")]})["letterboxd_slug"] == "ran"


def test_resolve_pin_drops_same_title_rows_with_no_director_column():
    """Can't confirm, so don't guess — fall through to the stored snapshot."""
    shows = pd.DataFrame(
        [
            {"letterboxd_title": "King Lear", "letterboxd_slug": "king-lear"},
            {"letterboxd_title": "King Lear", "letterboxd_slug": "king-lear-1987"},
        ]
    )
    stored = {"letterboxd_title": "King Lear", "directors": "Jean-Luc Godard"}

    assert resolve_pin(stored, shows) == stored


def test_pin_caption_formats_the_screening():
    caption = _pin_caption_html({"showtimes": pd.Timestamp("2026-08-04 18:00"), "theater_name": "Le Champo"})

    assert caption == "🎟 Tue 04 Aug · 18:00 — Le Champo"


def test_pin_caption_escapes_the_theater_name():
    caption = _pin_caption_html({"showtimes": "2026-08-04 18:00", "theater_name": "Ciné <b>X</b>"})

    assert "<b>" not in caption
    assert "&lt;b&gt;" in caption


@pytest.mark.parametrize(
    "pinned",
    [
        {},
        {"showtimes": None},
        {"showtimes": pd.NaT},
        {"showtimes": "not-a-date"},
    ],
    ids=["absent", "none", "nat", "unparseable"],
)
def test_pin_caption_is_empty_without_a_usable_date(pinned):
    assert _pin_caption_html(pinned) == ""


def test_pin_caption_omits_a_missing_theater():
    assert _pin_caption_html({"showtimes": "2026-08-04 18:00"}) == "🎟 Tue 04 Aug · 18:00"


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
    mocker.patch("chat.ui.st")  # the tool expanders need no real session here
    client = mocker.MagicMock()
    mocker.patch("chat.ui.genai.Client", return_value=client)
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
    spy = mocker.patch(f"chat.ui.{handler}", return_value=[{"title": "Ran"}])
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
