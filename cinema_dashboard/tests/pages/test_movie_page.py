"""Tests for the ``?movie=<slug>`` route and the detail page it renders.

The routing tests drive the real ``app.py`` through ``streamlit.testing.v1.AppTest``
— the only way to exercise the query-parameter branch *and* the ``st.navigation``
fallback together. ``config.settings`` is pointed at a throwaway parquet
directory first so no test ever reads this developer's real movie database.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from core.taste import build_affinity
from pages.movie import _contribution_rows_html, _text, _title_of
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent.parent / "app.py"

_FILM = {
    "slug": "solaris",
    "title": "Solaris",
    "french_title": "Solaris",
    "original_title": "Солярис",
    "tagline": "Who are we, out here?",
    "description": "A psychologist is sent to a station orbiting a distant planet.",
    "directors": "Andrei Tarkovsky",
    "writers": "Fridrikh Gorenshtein",
    "producers": "Viacheslav Tarasov",
    "studio": "Mosfilm",
    "cast": "Natalya Bondarchuk, Donatas Banionis",
    "genres": "Science Fiction, Drama",
    "themes": "Space, Grief",
    "mini_themes": "Memory",
    "country": "Soviet Union",
    "language": "Russian",
    "release_year": 1972,
    "runtime": 167.0,
    "letterboxd_avg_rating": 4.2,
    "poster_url": "https://example.com/solaris.jpg",
    "banner_url": "https://example.com/solaris-banner.jpg",
    "trailer_url": "",
    "tmdb_id": "593",
    "letterboxd_url": "https://letterboxd.com/film/solaris/",
    "imdb_url": "https://www.imdb.com/title/tt0069293/",
    "tmdb_url": "https://www.themoviedb.org/movie/593",
}

_OTHER = {**_FILM, "slug": "stalker", "title": "Stalker", "themes": "Faith", "mini_themes": None, "tmdb_id": "1398"}


@pytest.fixture
def movies_dir(tmp_path: Path) -> Path:
    """A throwaway OUTPUT_PATH holding the three parquets the page reads."""
    cache = pd.DataFrame([_FILM, _OTHER])
    cache.to_parquet(tmp_path / "data_letterboxd.parquet")
    pd.DataFrame([{**_FILM, "user_rating": 4.5}]).to_parquet(tmp_path / "ratings_with_letterboxd.parquet")
    pd.DataFrame([{**_OTHER, "user_rating": None}]).to_parquet(tmp_path / "watchlist_with_letterboxd.parquet")
    return tmp_path


@pytest.fixture
def app(mocker, movies_dir: Path) -> AppTest:
    """An ``AppTest`` over the real entry point, pointed at the throwaway parquets."""
    mocker.patch("config.settings.movies_output_path", movies_dir)
    mocker.patch("config.settings.allocine_output_path", None)
    return AppTest.from_file(str(APP_PATH), default_timeout=60)


def _markdown(at: AppTest) -> str:
    """Concatenate the app's markdown, minus the injected stylesheet.

    ``inject_css`` emits the whole design system as one ``st.markdown`` block,
    and it names every CSS class the page can render — so leaving it in would
    make "does this class appear?" assertions pass unconditionally.
    """
    return "\n".join(element.value for element in at.markdown if not element.value.startswith("<style>"))


# ── routing ─────────────────────────────────────────────────────────────────


def test_no_movie_param_runs_the_navigation_page(app: AppTest):
    """Without the parameter the app must behave exactly as before — Home, not a detail view."""
    app.run()

    assert not app.exception
    assert "Cinema Dashboard</h1>" in _markdown(app)
    assert "detail-back" not in _markdown(app)


def test_valid_slug_renders_the_detail_page(app: AppTest):
    app.query_params["movie"] = "solaris"
    app.run()

    assert not app.exception
    body = _markdown(app)
    assert "detail-back" in body
    assert "Solaris" in body
    assert "A psychologist is sent to a station" in body
    assert "Cinema Dashboard</h1>" not in body  # the nav page did not also run


def test_unknown_slug_renders_the_empty_state(app: AppTest):
    app.query_params["movie"] = "not-a-film"
    app.run()

    assert not app.exception
    body = _markdown(app)
    assert "No film at this link" in body
    assert "not-a-film" in body


def test_blank_slug_renders_the_empty_state(app: AppTest):
    """`?movie=` (truncated link) must explain itself rather than silently land on Home."""
    app.query_params["movie"] = ""
    app.run()

    assert not app.exception
    assert "No film at this link" in _markdown(app)


def test_missing_data_renders_the_empty_state(mocker):
    mocker.patch("config.settings.movies_output_path", None)
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.query_params["movie"] = "solaris"
    at.run()

    assert not at.exception
    assert "Letterboxd data missing" in _markdown(at)


# ── page sections ───────────────────────────────────────────────────────────


def test_detail_page_renders_the_sections_it_has_data_for(app: AppTest):
    app.query_params["movie"] = "solaris"
    app.run()
    body = _markdown(app)

    assert "Your verdict" in body
    assert "chip--user-rating" in body  # the user rated this one
    assert "Credits" in body
    assert "Andrei Tarkovsky" in body
    assert "Mosfilm" in body
    assert "Themes" in body
    assert "Memory" in body  # mini_themes fold into the theme chips
    assert "Who are we, out here?" in body  # tagline
    assert "letterboxd.com/film/solaris" in body  # out-links


def test_detail_page_omits_sections_without_data(app: AppTest):
    """trailer_url is null for ~2/3 of the cache — the section must vanish, not render empty."""
    app.query_params["movie"] = "solaris"
    app.run()

    assert "Trailer</div>" not in _markdown(app)
    assert not app.get("video")


def test_unrated_watchlist_film_shows_the_watchlist_state(app: AppTest):
    app.query_params["movie"] = "stalker"
    app.run()

    body = _markdown(app)
    assert "On your watchlist" in body
    assert "chip--user-rating" not in body


def test_detail_page_renders_the_more_like_this_rail(app: AppTest):
    app.query_params["movie"] = "solaris"
    app.run()

    assert "More like this" in _markdown(app)


def test_detail_page_renders_the_match_breakdown(app: AppTest):
    app.query_params["movie"] = "stalker"
    app.run()

    body = _markdown(app)
    assert "Taste match" in body
    assert "% match" in body
    assert "contrib-row" in body


def test_detail_page_lists_screenings_with_a_one_click_ics(mocker, movies_dir: Path, tmp_path: Path):
    """Screenings are keyed on the *cache*, so a rated (non-watchlist) film still lists them."""
    showtimes = tmp_path / "showtimes.parquet"
    pd.DataFrame(
        [
            {
                "theater_id": "T1",
                "theater_name": "Le Champo",
                "movie": "Solaris",
                "original_title": "Солярис",
                "director": "Andrei Tarkovsky",
                "runtime": "2h 47min",
                "release_year": 1972,
                "showtimes": pd.Timestamp("2030-08-03 19:30"),
            }
        ]
    ).to_parquet(showtimes)
    mocker.patch("config.settings.movies_output_path", movies_dir)
    mocker.patch("config.settings.allocine_output_path", showtimes)

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.query_params["movie"] = "solaris"
    at.run()

    assert not at.exception
    body = _markdown(at)
    assert "Upcoming screenings" in body
    assert "Le Champo" in body
    assert "Saturday 03 August · 19:30" in body
    assert [b.label for b in at.download_button] == ["📅 .ics"]


def test_detail_page_survives_an_unreadable_showtimes_file(mocker, movies_dir: Path, tmp_path: Path):
    """A malformed upstream parquet must cost the screenings section, not the page."""
    broken = tmp_path / "showtimes.parquet"
    broken.write_text("not a parquet file", encoding="utf-8")
    mocker.patch("config.settings.movies_output_path", movies_dir)
    mocker.patch("config.settings.allocine_output_path", broken)

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.query_params["movie"] = "solaris"
    at.run()

    assert not at.exception
    assert "Upcoming screenings" not in _markdown(at)
    assert "Solaris" in _markdown(at)


# ── page helpers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("Solaris", "Solaris"), ("  Solaris  ", "Solaris"), ("", ""), ("   ", ""), (None, ""), (float("nan"), ""), (7, "")],
)
def test_text_normalises_cells(cell, expected):
    assert _text(pd.Series({"title": cell}), "title") == expected


def test_title_of_prefers_the_letterboxd_title():
    assert _title_of(pd.Series({"title": "Solaris", "french_title": "Solaris (1972)"})) == "Solaris"


def test_title_of_falls_back_to_the_french_title():
    assert _title_of(pd.Series({"title": None, "french_title": "Le Masque arraché"})) == "Le Masque arraché"


def test_title_of_falls_back_to_untitled():
    assert _title_of(pd.Series({})) == "Untitled"


def test_contribution_rows_html_is_empty_without_known_values():
    profile = build_affinity(pd.DataFrame([{"user_rating": 3.0, "genres": "Drama"}]))

    assert _contribution_rows_html(pd.Series({"genres": "Western"}), profile) == ""


def test_contribution_rows_html_labels_sentiment_in_text_not_only_colour():
    """WCAG 1.4.1: the liked/disliked split must survive without the bar colour."""
    ratings = pd.DataFrame(
        [
            {"user_rating": 4.5, "genres": "Drama", "directors": "Alice", "release_year": 2000},
            {"user_rating": 0.5, "genres": "Western", "directors": "Bob", "release_year": 2000},
        ]
    )
    profile = build_affinity(ratings)

    liked = _contribution_rows_html(pd.Series({"genres": "Drama"}), profile)
    disliked = _contribution_rows_html(pd.Series({"genres": "Western"}), profile)

    assert "✓ Drama" in liked and "liked ·" in liked
    assert "✗ Western" in disliked and "disliked ·" in disliked


def test_contribution_rows_html_escapes_values():
    ratings = pd.DataFrame([{"user_rating": 4.0, "genres": "<script>", "directors": "Alice", "release_year": 2000}])
    profile = build_affinity(ratings)

    rendered = _contribution_rows_html(pd.Series({"genres": "<script>"}), profile)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
