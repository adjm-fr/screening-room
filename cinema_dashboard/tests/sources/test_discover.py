import pandas as pd

from sources.discover import WATCH_STATUS_LABELS, WATCH_STATUSES, build_screenings

# ---------------------------------------------------------------------------
# Fixtures (local — the shared make_showtimes/make_watchlist conftest
# fixtures model the watchlist parquet's shape, not the cache's)
# ---------------------------------------------------------------------------


def _showtimes(rows: list[dict]) -> pd.DataFrame:
    defaults = {"theater_id": "T1", "theater_name": "Le Champo"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _cache(rows: list[dict]) -> pd.DataFrame:
    defaults = {"runtime": 100, "genres": "Drama", "tmdb_id": "1"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _ratings(slugs: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"slug": slugs})


def _watchlist(slugs: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"slug": slugs})


_EMPTY = pd.DataFrame({"slug": pd.Series(dtype=str)})


# ---------------------------------------------------------------------------
# WATCH_STATUSES / WATCH_STATUS_LABELS
# ---------------------------------------------------------------------------


def test_watch_statuses_and_labels_in_sync():
    assert set(WATCH_STATUSES) == set(WATCH_STATUS_LABELS.keys())


def test_watch_statuses_contains_the_three_expected_values():
    assert set(WATCH_STATUSES) == {"untracked", "watchlist", "seen"}


# ---------------------------------------------------------------------------
# build_screenings — status labelling
# ---------------------------------------------------------------------------


def test_seen_status():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _ratings(["dune-2021"]), _EMPTY)
    assert len(result) == 1
    assert result.iloc[0]["watch_status"] == "seen"


def test_watchlist_status():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _EMPTY, _watchlist(["dune-2021"]))
    assert result.iloc[0]["watch_status"] == "watchlist"


def test_untracked_status_when_matched_but_not_rated_or_watchlisted():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert result.iloc[0]["watch_status"] == "untracked"


def test_seen_wins_over_watchlist():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _ratings(["dune-2021"]), _watchlist(["dune-2021"]))
    assert result.iloc[0]["watch_status"] == "seen"


# ---------------------------------------------------------------------------
# build_screenings — non-matching showtimes are dropped (inner join)
# ---------------------------------------------------------------------------


def test_showtime_with_no_cache_match_is_dropped():
    showtimes = _showtimes([{"movie": "Some Obscure Film", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "other-film", "title": "A Totally Different Film"}])
    assert build_screenings(showtimes, cache, _EMPTY, _EMPTY).empty


def test_empty_cache_drops_every_showtime():
    showtimes = _showtimes([{"movie": "Some Obscure Film", "showtimes": "2025-01-01 18:00"}])
    assert build_screenings(showtimes, _cache([]), _EMPTY, _EMPTY).empty


def test_director_confirmation_still_required_for_a_title_collision():
    # Title matches the cache, but the director doesn't confirm it — the row
    # must be dropped, not silently attached to the wrong film (mirrors
    # build_watchlist_showtimes's precision-first contract).
    showtimes = _showtimes([{"movie": "Nosferatu", "director": "Robert Eggers", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "nosferatu-1922", "title": "Nosferatu", "directors": "F.W. Murnau"}])
    assert build_screenings(showtimes, cache, _EMPTY, _EMPTY).empty


def test_only_the_matching_showtime_survives():
    showtimes = _showtimes(
        [
            {"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"},
            {"movie": "Unknown Film", "showtimes": "2025-01-01 20:00"},
        ]
    )
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Dune"


# ---------------------------------------------------------------------------
# build_screenings — user_rating (joined from the ratings parquet, not the cache)
# ---------------------------------------------------------------------------


def test_user_rating_is_joined_on_by_slug():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    ratings = pd.DataFrame({"slug": ["dune-2021"], "user_rating": [4.5]})
    result = build_screenings(showtimes, cache, ratings, _EMPTY)
    assert result.iloc[0]["user_rating"] == 4.5


def test_user_rating_is_na_for_an_unrated_film():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    ratings = pd.DataFrame({"slug": ["other-film"], "user_rating": [4.5]})
    result = build_screenings(showtimes, cache, ratings, _EMPTY)
    assert pd.isna(result.iloc[0]["user_rating"])


def test_user_rating_column_exists_even_without_a_ratings_parquet():
    # The page's rewatch/second-chance sections index this column
    # unconditionally, so it must be present (all-NA) rather than absent.
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert "user_rating" in result.columns
    assert pd.isna(result.iloc[0]["user_rating"])


# ---------------------------------------------------------------------------
# build_screenings — carries the linking slug through for matched rows
# ---------------------------------------------------------------------------


def test_matched_row_carries_letterboxd_slug():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert result.iloc[0]["letterboxd_slug"] == "dune-2021"


def test_matched_row_carries_tmdb_id_for_scoring():
    showtimes = _showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve", "tmdb_id": "438631"}])
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert result.iloc[0]["tmdb_id"] == "438631"


def test_cache_row_with_both_title_and_french_title_does_not_duplicate_columns():
    # Regression: when a matched cache row carries its own non-null
    # french_title (used only as a join key), renaming Allocine's "movie" to
    # "french_title" right after used to collide with it and produce two
    # identically-named columns — pandas then raises InvalidIndexError on the
    # concat with unmatched rows. Caught against the real parquets, where most
    # cache rows carry both title and french_title.
    showtimes = _showtimes(
        [{"movie": "Les Quatre Cents Coups", "director": "François Truffaut", "showtimes": "2025-01-01 18:00"}]
    )
    cache = _cache(
        [
            {
                "slug": "the-400-blows",
                "title": "The 400 Blows",
                "french_title": "Les Quatre Cents Coups",
                "directors": "François Truffaut",
            }
        ]
    )
    result = build_screenings(showtimes, cache, _EMPTY, _EMPTY)
    assert not result.columns.duplicated().any()
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Les Quatre Cents Coups"
    assert result.iloc[0]["letterboxd_title"] == "The 400 Blows"


def test_multiple_showtimes_for_same_film_all_labelled():
    showtimes = _showtimes(
        [
            {"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 14:00"},
            {"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 20:00"},
        ]
    )
    cache = _cache([{"slug": "dune-2021", "title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_screenings(showtimes, cache, _ratings(["dune-2021"]), _EMPTY)
    assert len(result) == 2
    assert result["watch_status"].tolist() == ["seen", "seen"]
