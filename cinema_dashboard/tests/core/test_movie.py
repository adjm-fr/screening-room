"""Tests for core.movie — the movie detail page's data assembly.

Pure pandas, no Streamlit: every function takes already-loaded frames, so
these run without a script run context.
"""

from __future__ import annotations

import pandas as pd
import pytest
from core.movie import MIN_SHARED_THEMES, load_movie, movie_screenings, similar_films, split_values


@pytest.fixture
def cache_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "slug": "solaris",
                "title": "Solaris",
                "directors": "Andrei Tarkovsky",
                "themes": "Space, Grief",
                "mini_themes": "Memory",
                "letterboxd_avg_rating": 4.2,
            },
            {
                "slug": "stalker",
                "title": "Stalker",
                "directors": "Andrei Tarkovsky",
                "themes": "Faith",
                "mini_themes": None,
                "letterboxd_avg_rating": 4.4,
            },
            {
                "slug": "gravity",
                "title": "Gravity",
                "directors": "Alfonso Cuarón",
                "themes": "Space, Grief",
                "mini_themes": None,
                "letterboxd_avg_rating": 3.6,
            },
            {
                "slug": "arrival",
                "title": "Arrival",
                "directors": "Denis Villeneuve",
                "themes": "Space",
                "mini_themes": None,
                "letterboxd_avg_rating": 3.9,
            },
            {
                "slug": "untracked",
                "title": "Untracked",
                "directors": "Nobody",
                "themes": None,
                "mini_themes": None,
                "letterboxd_avg_rating": 2.0,
            },
        ]
    )


@pytest.fixture
def ratings_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"slug": "solaris", "user_rating": 4.5},
            {"slug": "gravity", "user_rating": float("nan")},
        ]
    )


# ── split_values ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Space, Grief", ["Space", "Grief"]),
        ("  Space ,, Grief  ", ["Space", "Grief"]),
        ("Solo", ["Solo"]),
        ("", []),
        (None, []),
        (float("nan"), []),
        (42, []),
    ],
)
def test_split_values(cell, expected):
    assert split_values(cell) == expected


# ── load_movie ──────────────────────────────────────────────────────────────


def test_load_movie_returns_the_row(cache_df, ratings_df):
    movie = load_movie(cache_df, ratings_df, "stalker")

    assert movie is not None
    assert movie["title"] == "Stalker"


def test_load_movie_left_joins_the_user_rating(cache_df, ratings_df):
    movie = load_movie(cache_df, ratings_df, "solaris")

    assert movie is not None
    assert movie["user_rating"] == 4.5


def test_load_movie_unrated_film_still_carries_the_column(cache_df, ratings_df):
    """A watchlisted-but-unrated film must expose user_rating=None, not raise a KeyError."""
    movie = load_movie(cache_df, ratings_df, "arrival")

    assert movie is not None
    assert movie["user_rating"] is None


def test_load_movie_nan_rating_is_treated_as_unrated(cache_df, ratings_df):
    movie = load_movie(cache_df, ratings_df, "gravity")

    assert movie is not None
    assert movie["user_rating"] is None


def test_load_movie_cache_only_film(cache_df):
    """The cache carries a few hundred Allocine-enriched films in neither ratings nor watchlist."""
    movie = load_movie(cache_df, pd.DataFrame(), "untracked")

    assert movie is not None
    assert movie["title"] == "Untracked"
    assert movie["user_rating"] is None


def test_load_movie_unknown_slug_returns_none(cache_df, ratings_df):
    assert load_movie(cache_df, ratings_df, "not-a-film") is None


def test_load_movie_blank_slug_returns_none(cache_df, ratings_df):
    assert load_movie(cache_df, ratings_df, "") is None


def test_load_movie_empty_cache_returns_none(ratings_df):
    assert load_movie(pd.DataFrame(), ratings_df, "solaris") is None


def test_load_movie_cache_without_slug_column_returns_none(ratings_df):
    assert load_movie(pd.DataFrame([{"title": "Solaris"}]), ratings_df, "solaris") is None


def test_load_movie_ratings_without_user_rating_column(cache_df):
    movie = load_movie(cache_df, pd.DataFrame([{"slug": "solaris"}]), "solaris")

    assert movie is not None
    assert movie["user_rating"] is None


def test_load_movie_does_not_mutate_the_cache(cache_df, ratings_df):
    load_movie(cache_df, ratings_df, "solaris")

    assert "user_rating" not in cache_df.columns


# ── movie_screenings ────────────────────────────────────────────────────────


@pytest.fixture
def shows_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"letterboxd_slug": "solaris", "showtimes": "2026-08-04 21:00", "theater_name": "Le Champo"},
            {"letterboxd_slug": "solaris", "showtimes": "2026-08-03 19:30", "theater_name": "MK2 Beaubourg"},
            {"letterboxd_slug": "stalker", "showtimes": "2026-08-05 20:00", "theater_name": "Le Champo"},
        ]
    )


def test_movie_screenings_filters_to_the_film(shows_df):
    assert len(movie_screenings(shows_df, "solaris")) == 2


def test_movie_screenings_sorts_earliest_first(shows_df):
    rows = movie_screenings(shows_df, "solaris")

    assert rows["showtimes"].tolist() == ["2026-08-03 19:30", "2026-08-04 21:00"]


def test_movie_screenings_falls_back_to_the_slug_column():
    df = pd.DataFrame([{"slug": "solaris", "showtimes": "2026-08-03 19:30"}])

    assert len(movie_screenings(df, "solaris")) == 1


def test_movie_screenings_unknown_slug_is_empty(shows_df):
    assert movie_screenings(shows_df, "not-screening").empty


def test_movie_screenings_empty_frame_is_empty():
    assert movie_screenings(pd.DataFrame(), "solaris").empty


def test_movie_screenings_without_a_slug_column_is_empty():
    df = pd.DataFrame([{"showtimes": "2026-08-03 19:30"}])

    assert movie_screenings(df, "solaris").empty


def test_movie_screenings_blank_slug_is_empty(shows_df):
    assert movie_screenings(shows_df, "").empty


def test_movie_screenings_tolerates_unparseable_showtimes():
    df = pd.DataFrame([{"letterboxd_slug": "solaris", "showtimes": "not-a-date"}])

    assert len(movie_screenings(df, "solaris")) == 1


# ── similar_films ───────────────────────────────────────────────────────────


def test_similar_films_finds_the_same_director(cache_df):
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert "stalker" in similar_films(cache_df, movie)["slug"].tolist()


def test_similar_films_finds_shared_themes(cache_df):
    """Gravity shares Space + Grief with Solaris — two themes, so it qualifies."""
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert "gravity" in similar_films(cache_df, movie)["slug"].tolist()


def test_similar_films_ignores_a_single_shared_theme(cache_df):
    """Arrival shares only "Space"; one broad theme is not evidence of similarity."""
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert "arrival" not in similar_films(cache_df, movie)["slug"].tolist()
    assert MIN_SHARED_THEMES == 2


def test_similar_films_excludes_the_film_itself(cache_df):
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert "solaris" not in similar_films(cache_df, movie)["slug"].tolist()


def test_similar_films_ranks_the_director_match_first(cache_df):
    """Stalker (same director, lone theme) must outrank Gravity (two shared themes)."""
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert similar_films(cache_df, movie)["slug"].tolist()[0] == "stalker"


def test_similar_films_honours_the_limit(cache_df):
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert len(similar_films(cache_df, movie, limit=1)) == 1


def test_similar_films_without_metadata_is_empty(cache_df):
    movie = pd.Series({"slug": "solaris", "directors": None, "themes": None, "mini_themes": None})

    assert similar_films(cache_df, movie).empty


def test_similar_films_no_matches_is_empty(cache_df):
    movie = cache_df[cache_df["slug"] == "untracked"].iloc[0]

    assert similar_films(cache_df, movie).empty


def test_similar_films_empty_cache_is_empty(cache_df):
    movie = cache_df.iloc[0]

    assert similar_films(pd.DataFrame(), movie).empty


def test_similar_films_drops_its_scratch_columns(cache_df):
    movie = cache_df[cache_df["slug"] == "solaris"].iloc[0]

    assert not {"_shared_director", "_shared_themes"} & set(similar_films(cache_df, movie).columns)


def test_similar_films_matches_directors_case_insensitively(cache_df):
    movie = pd.Series({"slug": "other", "directors": "ANDREI TARKOVSKY", "themes": None, "mini_themes": None})

    assert "solaris" in similar_films(cache_df, movie)["slug"].tolist()
