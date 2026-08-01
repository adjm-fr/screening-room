"""Tests for ui.cards / ui.theme — movie card, poster rail, and hero card rendering (no Streamlit context needed)."""

from __future__ import annotations

import pandas as pd
import pytest
from ui import format_runtime, movie_href, rating_to_hsl, render_hero_card, render_poster_rail, row_slug
from ui.cards import _movie_card_html, _streaming_badges_html, _user_rating_chip_html

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


# ── trailer chip ─────────────────────────────────────────────────────────────


def test_movie_card_renders_trailer_chip():
    row = pd.Series({"title": "Solaris", "trailer_url": "https://www.youtube.com/watch?v=abc123"})
    card = _movie_card_html(row)
    assert "chip--trailer" in card
    assert "https://www.youtube.com/watch?v=abc123" in card
    assert "▶ Trailer" in card


def test_movie_card_omits_trailer_chip_when_missing():
    row = pd.Series({"title": "No Trailer Column"})
    assert "chip--trailer" not in _movie_card_html(row)


def test_movie_card_omits_trailer_chip_when_none():
    row = pd.Series({"title": "Untrailered", "trailer_url": None})
    assert "chip--trailer" not in _movie_card_html(row)


def test_movie_card_omits_trailer_chip_when_nan():
    row = pd.Series({"title": "Untrailered", "trailer_url": float("nan")})
    assert "chip--trailer" not in _movie_card_html(row)


def test_movie_card_escapes_trailer_url():
    row = pd.Series({"title": "XSS", "trailer_url": 'https://example.com/"><script>alert(1)</script>'})
    card = _movie_card_html(row)
    assert "<script>" not in card
    assert "&lt;script&gt;" in card


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


# ── render_poster_rail extra_html_fn ────────────────────────────────────────


def test_render_poster_rail_extra_html_fn_passthrough(mocker):
    markdown = mocker.patch("ui.cards.st.markdown")
    rows = pd.DataFrame([{"title": "Rio Lobo", "match": 90.0}])
    render_poster_rail(rows, title="Top matches", extra_html_fn=lambda r: f"<b>extra-{int(r['match'])}</b>")
    rendered = markdown.call_args[0][0]
    assert "extra-90" in rendered


# ── movie detail links ──────────────────────────────────────────────────────


def test_movie_href_is_relative_to_the_current_page():
    assert movie_href("goodbye-dragon-inn") == "?movie=goodbye-dragon-inn"


def test_movie_href_percent_encodes_the_slug():
    assert movie_href("a b&c") == "?movie=a%20b%26c"


def test_movie_href_escapes_quote_injection():
    assert '"' not in movie_href('x" onclick="alert(1)')


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"slug": "solaris"}, "solaris"),
        ({"letterboxd_slug": "solaris"}, "solaris"),
        # The showtimes join renames it; both spellings must resolve.
        ({"slug": "solaris", "letterboxd_slug": "stalker"}, "solaris"),
        ({"slug": "  solaris  "}, "solaris"),
        ({"slug": ""}, None),
        ({"slug": "   "}, None),
        ({"slug": None}, None),
        ({"slug": float("nan")}, None),
        ({"title": "No slug at all"}, None),
    ],
)
def test_row_slug(row, expected):
    assert row_slug(pd.Series(row)) == expected


def test_movie_card_title_links_to_the_detail_page():
    card = _movie_card_html(pd.Series({"title": "Solaris", "slug": "solaris"}))

    assert 'class="movie-card-link" href="?movie=solaris" target="_self"' in card
    assert "movie-card--linked" in card
    assert ">Solaris</a>" in card


def test_movie_card_links_off_the_joined_slug_column():
    """wl_shows carries the slug as letterboxd_slug — cards built from it must still link."""
    card = _movie_card_html(pd.Series({"letterboxd_title": "Solaris", "letterboxd_slug": "solaris"}))

    assert 'href="?movie=solaris"' in card


def test_movie_card_without_a_slug_renders_no_link():
    card = _movie_card_html(pd.Series({"title": "Solaris"}))

    assert "<a" not in card
    assert "movie-card--linked" not in card


def test_movie_card_never_nests_anchors():
    """The trailer chip is already an <a>; a second, wrapping anchor would be invalid HTML."""
    row = pd.Series({"title": "Solaris", "slug": "solaris", "trailer_url": "https://youtu.be/x"})
    card = _movie_card_html(row)

    assert card.count("<a ") == 2  # the title link and the trailer chip, and no more
    title_open = card.index('<a class="movie-card-link"')
    title_close = card.index("</a>", title_open)
    trailer_open = card.index('<a class="chip chip--trailer"')
    assert trailer_open > title_close  # siblings, not nested
    assert "<a" not in card[card.index(">", title_open) + 1 : title_close]


def test_render_hero_card_links_the_whole_hero(mocker):
    markdown = mocker.patch("ui.cards.st.markdown")
    render_hero_card(pd.Series({"title": "Solaris", "letterboxd_slug": "solaris"}))
    rendered = markdown.call_args[0][0]

    assert 'class="hero-link" href="?movie=solaris" target="_self"' in rendered
    assert "hero-card--linked" in rendered
    assert 'aria-label="Solaris — open film details"' in rendered


def test_render_hero_card_without_a_slug_renders_no_link(mocker):
    markdown = mocker.patch("ui.cards.st.markdown")
    render_hero_card(pd.Series({"title": "Solaris"}))
    rendered = markdown.call_args[0][0]

    assert "hero-link" not in rendered
    assert "hero-card--linked" not in rendered


def test_render_hero_card_does_not_hide_its_body_from_screen_readers(mocker):
    """role="img" on the container would suppress the title, meta line and link inside it."""
    markdown = mocker.patch("ui.cards.st.markdown")
    render_hero_card(pd.Series({"title": "Solaris", "slug": "solaris"}))

    assert 'role="img"' not in markdown.call_args[0][0]
