"""Tests for chat.pins — which films a reply offers, and which film a pin means.

All pure pandas: no Streamlit, no Gemini. The two rules these exist to protect are
that a title does not identify a film (King Lear is Brook's *and* Godard's, so every
title match is confirmed by director) and that the candidate set spans the whole
closed set the model can name — screenings *and* streaming.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chat.pins import _assistant_text, _find_pinnable_titles, _pin_row, _streamable, resolve_pin


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


@pytest.fixture
def pinnable_shows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"letterboxd_title": "A Special Day", "french_title": "Une journée particulière", "letterboxd_slug": "a-special-day"},
            {"letterboxd_title": "Dark Passage", "french_title": "Les Passagers de la nuit", "letterboxd_slug": "dark-passage"},
        ]
    )


@pytest.fixture
def pinnable_streaming() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "letterboxd_title": "The Power of the Dog",
                "french_title": "Le Pouvoir du chien",
                "slug": "tpotd",
                "flatrate": ["netflix"],
                "free": [],
            },
            {"letterboxd_title": "Not Streaming", "french_title": "Pas en ligne", "slug": "nope", "flatrate": [], "free": []},
        ]
    )


def test_find_pinnable_titles_matches_a_reply(pinnable_shows):
    assert _find_pinnable_titles("**A Special Day** is showing at L'Archipel.", pinnable_shows) == ["A Special Day"]


def test_find_pinnable_titles_matches_the_french_spelling(pinnable_shows):
    """The prompt feeds the model both spellings, so it answers with either."""
    assert _find_pinnable_titles("Les Passagers de la nuit passe au Christine.", pinnable_shows) == ["Dark Passage"]


def test_find_pinnable_titles_returns_one_entry_when_both_spellings_appear(pinnable_shows):
    """ "Dark Passage (Les Passagers de la nuit)" is one film, not two options."""
    assert _find_pinnable_titles("Dark Passage (Les Passagers de la nuit)", pinnable_shows) == ["Dark Passage"]


def test_find_pinnable_titles_spans_every_candidate_frame(pinnable_shows, pinnable_streaming):
    """The #53 case: a Netflix recommendation was unpinnable — it never screens."""
    reply = "A Special Day is at L'Archipel; The Power of the Dog is on Netflix."

    pinnable = _find_pinnable_titles(reply, pinnable_shows, _streamable(pinnable_streaming))

    assert pinnable == ["A Special Day", "The Power of the Dog"]


def test_find_pinnable_titles_matches_whole_words_only(pinnable_shows):
    """Bare substrings make every short title fire — " ran " is inside "grand rex"."""
    shows = pd.DataFrame([{"letterboxd_title": "Ran", "french_title": "Ran"}])

    assert _find_pinnable_titles("Playing at Le Grand Rex tonight.", shows) == []


def test_find_pinnable_titles_ignores_an_unmentioned_film(pinnable_shows):
    assert _find_pinnable_titles("Nothing on your watchlist fits tonight.", pinnable_shows) == []


def test_find_pinnable_titles_tolerates_frames_it_cannot_read():
    assert _find_pinnable_titles("A Special Day", pd.DataFrame(), pd.DataFrame([{"title": "A Special Day"}])) == []


def test_streamable_keeps_only_films_with_a_provider(pinnable_streaming):
    assert _streamable(pinnable_streaming)["letterboxd_title"].tolist() == ["The Power of the Dog"]


def test_streamable_counts_free_providers_too():
    """Free platforms are watchable by everyone — never gated behind a subscription."""
    df = pd.DataFrame([{"letterboxd_title": "Faces Places", "flatrate": [], "free": ["arte"]}])

    assert len(_streamable(df)) == 1


def test_streamable_on_a_frame_without_the_columns_is_empty():
    assert _streamable(pd.DataFrame([{"letterboxd_title": "A"}])).empty


def test_assistant_text_spans_the_whole_transcript():
    """Earlier replies stay pinnable — asking a follow-up must not un-offer them."""
    messages = [
        {"role": "user", "content": "what's on this weekend?"},
        {"role": "assistant", "content": "A Special Day"},
        {"role": "user", "content": "and on Netflix?"},
        {"role": "assistant", "content": "The Power of the Dog"},
    ]

    assert _assistant_text(messages) == "A Special Day\nThe Power of the Dog"


def test_pin_row_prefers_the_screening_frame(pinnable_shows):
    """A film that both screens and streams pins with its showtime, not without."""
    streaming = pd.DataFrame([{"letterboxd_title": "A Special Day", "flatrate": ["netflix"]}])

    assert _pin_row("A Special Day", pinnable_shows, streaming)["letterboxd_slug"] == "a-special-day"


def test_pin_row_falls_through_to_the_streaming_frame(pinnable_shows, pinnable_streaming):
    row = _pin_row("The Power of the Dog", pinnable_shows, _streamable(pinnable_streaming))

    assert row is not None
    assert row["slug"] == "tpotd"


def test_pin_row_is_none_for_an_unknown_title(pinnable_shows):
    assert _pin_row("Solaris", pinnable_shows, pd.DataFrame()) is None
