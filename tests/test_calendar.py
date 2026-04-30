"""Tests for the watchlist↔showtimes join and the calendar event builder."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))
from pages.calendar import _to_calendar_events  # noqa: E402
from utils.data_loader import _normalize_directors, build_watchlist_showtimes  # noqa: E402


def _showtimes(rows: list[dict]) -> pd.DataFrame:
    defaults = {"theater_id": "T1", "theater_name": "Cinema"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _watchlist(rows: list[dict]) -> pd.DataFrame:
    defaults = {"slug": "test-slug", "runtime": 100, "genres": "Drama"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _events_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ── build_watchlist_showtimes ─────────────────────────────────────────────────


def test_exact_match():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Dune"


def test_case_insensitive_match():
    showtimes = _showtimes([{"movie": "DUNE", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_no_match_returns_empty():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Oppenheimer"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_original_title_key_allocine_vs_letterboxd_title():
    # Allocine original_title matches Letterboxd title (no original_title on LB side → title is the key)
    showtimes = _showtimes([{"movie": "Dune: Deuxième Partie", "original_title": "Dune: Part Two", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune: Part Two"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_original_title_matched_on_both_sides():
    # Both sides have original_title with accent → keys match exactly
    showtimes = _showtimes([{"movie": "Détective", "original_title": "Détective", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Detective", "original_title": "Détective"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_movie_used_when_original_title_absent():
    # No original_title in showtimes → movie is the key
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_runtime_column_renamed():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune", "runtime": 155}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "runtime_minutes" in result.columns
    assert "runtime" not in result.columns


def test_runtime_from_watchlist_not_scraper():
    # Both sources have a runtime column; watchlist value (155) must win
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00", "runtime": 999}])
    watchlist = _watchlist([{"title": "Dune", "runtime": 155}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.iloc[0]["runtime_minutes"] == 155


def test_slug_column_dropped():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune", "slug": "dune-2021"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "letterboxd_slug" not in result.columns
    assert "slug" not in result.columns


def test_multiple_showtimes_for_same_movie():
    showtimes = _showtimes(
        [
            {"movie": "Dune", "showtimes": "2025-01-01 14:00"},
            {"movie": "Dune", "showtimes": "2025-01-01 20:00"},
        ]
    )
    watchlist = _watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 2


def test_key_column_not_in_output():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "_key" not in result.columns


# ── director-aware merge ──────────────────────────────────────────────────────


def test_director_match_single():
    showtimes = _showtimes([{"movie": "Obsession", "director": "Brian De Palma", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Obsession", "directors": "Brian De Palma"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_match_multiple():
    showtimes = _showtimes([{"movie": "No Country", "director": "Joel Coen | Ethan Coen", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "No Country", "directors": "Ethan Coen, Joel Coen"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_partial_overlap_kept():
    # Allocine and Letterboxd disagree on one co-director but share one → keep
    showtimes = _showtimes([{"movie": "The Kid Brother", "director": "Harold Lloyd | Lewis Milestone", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "The Kid Brother", "directors": "Ted Wilde, Harold Lloyd"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_no_overlap_filtered_out():
    showtimes = _showtimes([{"movie": "Obsession", "director": "Brian De Palma", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Obsession", "directors": "Edward Dmytryk"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_director_missing_one_side_falls_back_to_title():
    # showtimes has no director column → title-only match, should still return 1 row
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_nan_value_keeps_title_match():
    # director column exists but value is NaN for this row → should not filter out the match
    showtimes = _showtimes([{"movie": "Dune", "director": float("nan"), "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_case_and_accent_normalised():
    showtimes = _showtimes([{"movie": "Nikita", "director": "luc besson", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Nikita", "directors": "Luc Besson"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


# ── _normalize_directors ──────────────────────────────────────────────────────


def test_normalize_directors_pipe_sep():
    # Same names, different separators and order → identical frozensets
    assert _normalize_directors("Joel Coen | Ethan Coen", sep=" | ") == _normalize_directors("Ethan Coen, Joel Coen", sep=", ")


def test_normalize_directors_accent():
    assert _normalize_directors("Léos Carax", sep=", ") == _normalize_directors("Leos Carax", sep=", ")


def test_normalize_directors_empty():
    assert _normalize_directors(None, sep=", ") == set()
    assert _normalize_directors("", sep=", ") == set()


def test_normalize_directors_partial_overlap():
    # Simulates Allocine vs Letterboxd discrepancy (e.g. The Kid Brother)
    allocine = _normalize_directors("Harold Lloyd | Lewis Milestone", sep=" | ")
    letterboxd = _normalize_directors("Ted Wilde, Harold Lloyd", sep=", ")
    assert allocine & letterboxd == {"harold lloyd"}


# ── _to_calendar_events ───────────────────────────────────────────────────────


def test_basic_event_shape():
    df = _events_df(
        [
            {
                "french_title": "Dune",
                "showtimes": "2025-06-01 18:00",
                "runtime_minutes": 150,
                "theater_name": "UGC",
            }
        ]
    )
    events = _to_calendar_events(df)
    assert len(events) == 1
    e = events[0]
    assert e["title"] == "Dune"
    assert e["color"] == "#e63946"
    assert "start" in e and "end" in e
    assert e["extendedProps"]["theater"] == "UGC"


def test_end_time_computed_from_runtime():
    df = _events_df(
        [
            {
                "french_title": "Dune",
                "showtimes": "2025-06-01 18:00",
                "runtime_minutes": 60,
                "theater_name": "UGC",
            }
        ]
    )
    events = _to_calendar_events(df)
    start = pd.Timestamp(events[0]["start"])
    end = pd.Timestamp(events[0]["end"])
    assert (end - start).total_seconds() == 3600


def test_missing_runtime_defaults_to_120min():
    df = _events_df(
        [
            {
                "french_title": "Dune",
                "showtimes": "2025-06-01 18:00",
                "runtime_minutes": None,
                "theater_name": "UGC",
            }
        ]
    )
    events = _to_calendar_events(df)
    start = pd.Timestamp(events[0]["start"])
    end = pd.Timestamp(events[0]["end"])
    assert (end - start).total_seconds() == 7200


def test_nat_showtime_skipped():
    df = _events_df(
        [
            {"french_title": "Dune", "showtimes": pd.NaT, "runtime_minutes": 100, "theater_name": "UGC"},
            {"french_title": "Oppenheimer", "showtimes": "2025-06-01 20:00", "runtime_minutes": 180, "theater_name": "UGC"},
        ]
    )
    events = _to_calendar_events(df)
    assert len(events) == 1
    assert events[0]["title"] == "Oppenheimer"


def test_theater_id_fallback_when_name_missing():
    df = _events_df(
        [
            {
                "french_title": "Dune",
                "showtimes": "2025-06-01 18:00",
                "runtime_minutes": 100,
                "theater_id": "P1234",
            }
        ]
    )
    events = _to_calendar_events(df)
    assert events[0]["extendedProps"]["theater"] == "P1234"


def test_empty_dataframe_returns_empty_list():
    df = pd.DataFrame(columns=["french_title", "showtimes", "runtime_minutes", "theater_name"])
    assert _to_calendar_events(df) == []
