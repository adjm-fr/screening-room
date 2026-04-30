"""Tests for the watchlist↔showtimes join and the calendar event builder."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))
from pages.calendar import _to_calendar_events  # noqa: E402
from utils.data_loader import build_watchlist_showtimes  # noqa: E402


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
    assert result.iloc[0]["movie"] == "Dune"


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


def test_original_title_fallback():
    showtimes = _showtimes(
        [
            {"movie": "Dune: Part Two", "original_title": "Dune: Deuxième Partie", "showtimes": "2025-01-01 18:00"},
        ]
    )
    watchlist = _watchlist([{"title": "Dune: Deuxième Partie"}])
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


def test_slug_column_renamed():
    showtimes = _showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = _watchlist([{"title": "Dune", "slug": "dune-2021"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "letterboxd_slug" in result.columns
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


# ── _to_calendar_events ───────────────────────────────────────────────────────


def test_basic_event_shape():
    df = _events_df(
        [
            {
                "movie": "Dune",
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
                "movie": "Dune",
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
                "movie": "Dune",
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
            {"movie": "Dune", "showtimes": pd.NaT, "runtime_minutes": 100, "theater_name": "UGC"},
            {"movie": "Oppenheimer", "showtimes": "2025-06-01 20:00", "runtime_minutes": 180, "theater_name": "UGC"},
        ]
    )
    events = _to_calendar_events(df)
    assert len(events) == 1
    assert events[0]["title"] == "Oppenheimer"


def test_theater_id_fallback_when_name_missing():
    df = _events_df(
        [
            {
                "movie": "Dune",
                "showtimes": "2025-06-01 18:00",
                "runtime_minutes": 100,
                "theater_id": "P1234",
            }
        ]
    )
    events = _to_calendar_events(df)
    assert events[0]["extendedProps"]["theater"] == "P1234"


def test_empty_dataframe_returns_empty_list():
    df = pd.DataFrame(columns=["movie", "showtimes", "runtime_minutes", "theater_name"])
    assert _to_calendar_events(df) == []
