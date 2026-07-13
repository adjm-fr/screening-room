"""Tests for utils.ui — pure helpers (no Streamlit context required)."""

from __future__ import annotations

import re

import pandas as pd
import pytest
from utils.taste import TasteProfile
from utils.ui import (
    _ics_escape,
    _movie_card_html,
    _streaming_badges_html,
    _user_rating_chip_html,
    format_runtime,
    match_chips_html,
    rating_to_hsl,
    render_poster_rail,
    to_ics,
)

# ── format_runtime ──────────────────────────────────────────────────────────


def test_format_runtime_zero_returns_em_dash():
    assert format_runtime(0) == "—"


def test_format_runtime_none_returns_em_dash():
    assert format_runtime(None) == "—"


def test_format_runtime_invalid_string_returns_em_dash():
    assert format_runtime("not a number") == "—"  # type: ignore[arg-type]


def test_format_runtime_one_hour():
    assert format_runtime(60) == "1h00"


def test_format_runtime_two_hours_twelve():
    assert format_runtime(132) == "2h12"


def test_format_runtime_float_input():
    assert format_runtime(132.7) == "2h12"


def test_format_runtime_under_one_hour():
    assert format_runtime(45) == "0h45"


def test_format_runtime_preformatted_string():
    """Already-formatted strings should pass through."""
    assert format_runtime("1h 25min") == "1h 25min"


def test_format_runtime_preformatted_string_with_hour_suffix():
    """String with 'h' should be recognized as pre-formatted."""
    assert format_runtime("2h30") == "2h30"


# ── rating_to_hsl ───────────────────────────────────────────────────────────


def test_rating_to_hsl_none_is_transparent():
    assert rating_to_hsl(None) == "transparent"


def test_rating_to_hsl_high_score_dark():
    # rating 10 → lightness 80 - 10*4 = 40
    assert rating_to_hsl(10) == "hsl(36 80% 40%)"


def test_rating_to_hsl_low_score_light():
    assert rating_to_hsl(0) == "hsl(36 80% 80%)"


def test_rating_to_hsl_clamps_above_ten():
    assert rating_to_hsl(15) == "hsl(36 80% 40%)"


def test_rating_to_hsl_clamps_below_zero():
    assert rating_to_hsl(-3) == "hsl(36 80% 80%)"


def test_rating_to_hsl_invalid_string_is_transparent():
    assert rating_to_hsl("not a number") == "transparent"  # type: ignore[arg-type]


def test_rating_to_hsl_custom_hue_and_scale():
    # 5 on a 0-5 scale is the top of the ramp → darkest lightness (40%) at the green hue.
    assert rating_to_hsl(5, hue=145, scale_max=5.0) == "hsl(145 80% 40%)"


# ── _user_rating_chip_html ──────────────────────────────────────────────────


def test_user_rating_chip_empty_for_none():
    assert _user_rating_chip_html(None) == ""


def test_user_rating_chip_empty_for_nan():
    assert _user_rating_chip_html(float("nan")) == ""


def test_user_rating_chip_is_green_and_labelled():
    html_out = _user_rating_chip_html(4.5)
    assert "chip--user-rating" in html_out
    assert "hsl(145" in html_out  # green hue, not the amber default
    assert "★ 4.5" in html_out
    assert 'aria-label="Your rating: 4.5 out of 5"' in html_out


def test_movie_card_renders_user_rating_chip():
    row = pd.Series({"title": "Solaris", "user_rating": 4.0, "letterboxd_avg_rating": 3.8})
    card = _movie_card_html(row)
    assert "chip--user-rating" in card  # the user's green chip
    assert "hsl(145" in card  # green user chip
    assert "hsl(36" in card  # amber Letterboxd-average chip still present


def test_movie_card_omits_user_rating_chip_when_absent():
    row = pd.Series({"title": "Unrated", "letterboxd_avg_rating": 3.8})
    assert "chip--user-rating" not in _movie_card_html(row)


def test_rating_chip_uses_five_point_scale():
    # A perfect 5-star Letterboxd average is the top of the ramp → darkest amber (40%).
    row = pd.Series({"title": "Stalker", "letterboxd_avg_rating": 5.0})
    assert "hsl(36 80% 40%)" in _movie_card_html(row)


# ── _ics_escape ─────────────────────────────────────────────────────────────


def test_ics_escape_comma():
    assert _ics_escape("a,b") == "a\\,b"


def test_ics_escape_semicolon():
    assert _ics_escape("a;b") == "a\\;b"


def test_ics_escape_newline():
    assert _ics_escape("line1\nline2") == "line1\\nline2"


def test_ics_escape_backslash_first():
    assert _ics_escape("a\\b") == "a\\\\b"


# ── to_ics ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_events():
    return [
        {
            "summary": "Parasite",
            "start": "2026-05-10T19:30:00",
            "end": "2026-05-10T21:42:00",
            "location": "Le Champo",
            "description": "Bong Joon-ho",
            "uid": "fixed-uid-1",
        },
        {
            "summary": "Drive, My Car",
            "start": "2026-05-11T20:00:00",
            "end": "2026-05-11T22:59:00",
            "uid": "fixed-uid-2",
        },
    ]


def test_to_ics_starts_and_ends_correctly(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.rstrip("\r\n").endswith("END:VCALENDAR")


def test_to_ics_one_vevent_per_input(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert out.count("BEGIN:VEVENT") == 2
    assert out.count("END:VEVENT") == 2


def test_to_ics_dtstart_floating_local_format(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    # RFC 5545 floating-local form: YYYYMMDDTHHMMSS (no Z, no TZID)
    assert re.search(r"DTSTART:20260510T193000", out)


def test_to_ics_summary_with_comma_is_escaped(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert "SUMMARY:Drive\\, My Car" in out


def test_to_ics_uses_provided_uid(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert "UID:fixed-uid-1" in out
    assert "UID:fixed-uid-2" in out


def test_to_ics_generates_uid_when_missing():
    events = [{"summary": "X", "start": "2026-01-01T10:00:00", "end": "2026-01-01T12:00:00"}]
    out = to_ics(events).decode("utf-8")
    assert re.search(r"UID:[0-9a-f-]+@cinema_dashboard", out)


def test_to_ics_crlf_line_endings(sample_events):
    out = to_ics(sample_events)
    assert b"\r\n" in out
    # No bare LF without preceding CR — RFC 5545 §3.1
    assert not re.search(rb"(?<!\r)\n", out)


def test_to_ics_round_trips_utf8():
    events = [
        {
            "summary": "Amélie",
            "start": "2026-01-01T10:00:00",
            "end": "2026-01-01T12:00:00",
            "location": "Cinéma Saint-Germain",
        }
    ]
    out = to_ics(events).decode("utf-8")
    assert "Amélie" in out
    assert "Cinéma Saint-Germain" in out


def test_to_ics_omits_optional_fields_when_missing():
    events = [{"summary": "X", "start": "2026-01-01T10:00:00", "end": "2026-01-01T12:00:00"}]
    out = to_ics(events).decode("utf-8")
    assert "LOCATION" not in out
    assert "DESCRIPTION" not in out


# ── _streaming_badges_html ──────────────────────────────────────────────────


def test_streaming_badges_empty_when_no_data():
    assert _streaming_badges_html([], [], {"mubi"}) == ""
    assert _streaming_badges_html(None, None, {"mubi"}) == ""


def test_streaming_badges_empty_when_no_subscription_match():
    # flatrate present but subscriber owns none of those services, and no free
    # providers either → hide.
    assert _streaming_badges_html(["netflix"], [], {"mubi"}) == ""


def test_streaming_badges_subscribed_filled_first():
    out = _streaming_badges_html(["mubi", "netflix"], [], {"mubi"})
    assert 'class="chip chip--streaming"' in out
    # Only subscribed service shows up filled; non-subscribed flatrate is hidden.
    # Badges render the human-readable display name, not the raw slug.
    assert ">MUBI<" in out
    assert "netflix" not in out.lower()


def test_streaming_badges_tolerates_nan_inputs():
    import math

    assert _streaming_badges_html(math.nan, math.nan, {"mubi"}) == ""


def test_streaming_badges_free_renders_regardless_of_subscription():
    # Free providers show up even with no matching (or no) subscription.
    out = _streaming_badges_html([], ["arte"], set())
    assert 'class="chip chip--streaming-free"' in out
    assert "ARTE (free)" in out


def test_streaming_badges_free_and_subscribed_flatrate_both_render():
    out = _streaming_badges_html(["mubi"], ["arte"], {"mubi"})
    assert 'class="chip chip--streaming"' in out
    assert 'class="chip chip--streaming-free"' in out
    assert ">MUBI<" in out
    assert "ARTE (free)" in out


# ── match_chips_html / render_poster_rail extra_html_fn ────────────────────


def _make_profile() -> TasteProfile:
    return TasteProfile(
        mu=3.0,
        n_ratings=10,
        affinities={"directors": {"Alfred Hitchcock": 0.9}, "genres": {"Western": 0.5, "Comedy": -0.5}},
        counts={},
    )


def test_match_chips_html_contains_text_and_classes():
    row = pd.Series({"match": 72.4, "directors": "Alfred Hitchcock", "genres": "Western"})
    out = match_chips_html(row, _make_profile())
    assert 'class="chip chip--match"' in out
    assert "◎ 72% match" in out
    assert 'class="chip chip--why"' in out
    assert "✓ Alfred Hitchcock" in out


def test_match_chips_html_empty_when_no_match_value():
    profile = _make_profile()
    assert match_chips_html(pd.Series({"match": float("nan")}), profile) == ""
    assert match_chips_html(pd.Series({"title": "X"}), profile) == ""


def test_match_chips_html_badge_only_when_no_positive_contributors():
    row = pd.Series({"match": 31.0, "genres": "Comedy"})
    out = match_chips_html(row, _make_profile())
    assert "% match" in out
    assert "chip--why" not in out


def test_render_poster_rail_extra_html_fn_passthrough(mocker):
    markdown = mocker.patch("utils.ui.st.markdown")
    rows = pd.DataFrame([{"title": "Rio Lobo", "match": 90.0}])
    render_poster_rail(rows, title="Top matches", extra_html_fn=lambda r: f"<b>extra-{int(r['match'])}</b>")
    rendered = markdown.call_args[0][0]
    assert "extra-90" in rendered
