"""Tests for core.agenda — the calendar page's filtering, grouping and labelling.

Streamlit-free by design, so everything here runs without an app context.

2026 anchors used throughout: Tue 2026-08-04 is "today"; Wed 2026-08-05 is a
plain working day; Sat 2026-08-08 is a weekend. None of them is a French public
holiday (the nearest is Assumption, 15 August).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.agenda import (
    AgendaFilters,
    DayChip,
    agenda_kpis,
    apply_day,
    apply_filters,
    build_agenda,
    day_chip_label,
    day_chips,
    day_label,
    runtime_bucket,
    time_bucket,
    with_agenda_columns,
)

TODAY = dt.date(2026, 8, 4)


def _row(**overrides) -> dict:
    base = {
        "showtimes": "2026-08-04 19:00",
        "theater_name": "Le Champo",
        "letterboxd_slug": "vertigo",
        "letterboxd_title": "Vertigo",
        "french_title": "Sueurs froides",
        "directors": "Alfred Hitchcock",
        "letterboxd_avg_rating": 4.5,
        "runtime_minutes": 128.0,
    }
    base.update(overrides)
    return base


def _frame(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame([_row(**r) for r in rows])


# ── runtime_bucket ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (89, "<90"),
        (90, "90–120"),
        (120, "90–120"),
        (121, ">120"),
        (112.7, "90–120"),  # truncated, not rounded
        (None, "Unknown"),
        (float("nan"), "Unknown"),
        ("", "Unknown"),
        ("abc", "Unknown"),
        ("112", "90–120"),
        ("95 min", "90–120"),
        ("1h", "<90"),
        ("2h12", ">120"),  # the format_runtime round-trip shape
    ],
)
def test_runtime_bucket(value, expected):
    assert runtime_bucket(value) == expected


def test_runtime_bucket_parses_hours_and_minutes_separately():
    """Regression: stripping non-digits turned "1h 52min" into 152, not 112."""
    assert runtime_bucket("1h 52min") == "90–120"


# ── time_bucket ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-08-04 09:30", "Morning"),
        ("2026-08-04 11:59", "Morning"),
        ("2026-08-04 12:00", "Afternoon"),
        ("2026-08-04 17:59", "Afternoon"),
        ("2026-08-04 18:00", "Evening"),
        ("2026-08-04 21:59", "Evening"),
        ("2026-08-04 22:00", "Late"),
        ("2026-08-04 23:59", "Late"),
    ],
)
def test_time_bucket(stamp, expected):
    assert time_bucket(pd.Timestamp(stamp)) == expected


def test_time_bucket_handles_nat():
    assert time_bucket(pd.NaT) == "Unknown"


# ── day labels ───────────────────────────────────────────────────────────────


def test_day_label_friendly_names():
    assert day_label(dt.date(2026, 8, 4), today=TODAY) == "Tonight"
    assert day_label(dt.date(2026, 8, 5), today=TODAY) == "Tomorrow"
    assert day_label(dt.date(2026, 8, 7), today=TODAY) == "Friday 07 August"


def test_day_chip_label_has_no_zero_padding():
    assert day_chip_label(dt.date(2026, 8, 4)) == "Tue 4"
    assert day_chip_label(dt.date(2026, 8, 12)) == "Wed 12"


# ── with_agenda_columns ──────────────────────────────────────────────────────


def test_with_agenda_columns_adds_derived_columns():
    out = with_agenda_columns(_frame({}))
    for col in ("_dt", "_day", "_time_bucket", "_runtime_bucket", "_film_key"):
        assert col in out.columns
    assert out.iloc[0]["_day"] == dt.date(2026, 8, 4)
    assert out.iloc[0]["_time_bucket"] == "Evening"
    assert out.iloc[0]["_runtime_bucket"] == ">120"
    assert out.iloc[0]["_film_key"] == "vertigo"


def test_with_agenda_columns_drops_unplaceable_showtimes():
    out = with_agenda_columns(_frame({}, {"showtimes": None}, {"showtimes": "not a date"}))
    assert len(out) == 1


def test_with_agenda_columns_is_idempotent():
    once = with_agenda_columns(_frame({}))
    assert with_agenda_columns(once) is once


def test_with_agenda_columns_does_not_mutate_input():
    df = _frame({})
    with_agenda_columns(df)
    assert "_dt" not in df.columns


def test_film_key_falls_back_through_titles():
    out = with_agenda_columns(
        _frame(
            {"letterboxd_slug": None, "letterboxd_title": "Vertigo"},
            {"letterboxd_slug": None, "letterboxd_title": None, "french_title": "Sueurs froides"},
            {"letterboxd_slug": None, "letterboxd_title": None, "french_title": None},
        )
    )
    assert out["_film_key"].tolist() == ["Vertigo", "Sueurs froides", ""]


def test_with_agenda_columns_on_empty_frame():
    out = with_agenda_columns(pd.DataFrame(columns=["showtimes", "theater_name"]))
    assert out.empty
    assert "_film_key" in out.columns


# ── apply_filters ────────────────────────────────────────────────────────────


def test_default_filters_are_the_identity():
    df = _frame({}, {"showtimes": "2026-08-05 20:00"})
    assert len(apply_filters(df, AgendaFilters())) == 2


def test_filter_by_theater():
    df = _frame({}, {"theater_name": "MK2 Bibliothèque"})
    out = apply_filters(df, AgendaFilters(theaters=("MK2 Bibliothèque",)))
    assert out["theater_name"].tolist() == ["MK2 Bibliothèque"]


def test_filter_by_runtime_bucket():
    df = _frame({"runtime_minutes": 80.0}, {"runtime_minutes": 128.0})
    out = apply_filters(df, AgendaFilters(runtimes=("<90",)))
    assert out["runtime_minutes"].tolist() == [80.0]


def test_filter_by_time_bucket():
    df = _frame({"showtimes": "2026-08-04 10:00"}, {"showtimes": "2026-08-04 20:00"})
    out = apply_filters(df, AgendaFilters(time_buckets=("Evening",)))
    assert out["_dt"].dt.hour.tolist() == [20]


def test_filter_by_min_rating_treats_missing_as_zero():
    df = _frame({"letterboxd_avg_rating": 4.5}, {"letterboxd_avg_rating": 2.0}, {"letterboxd_avg_rating": None})
    out = apply_filters(df, AgendaFilters(min_rating=3.0))
    assert out["letterboxd_avg_rating"].tolist() == [4.5]


@pytest.mark.parametrize("needle", ["vertigo", "SUEURS", "hitchcock"])
def test_search_matches_both_titles_and_directors(needle):
    df = _frame({}, {"letterboxd_slug": "mandy", "letterboxd_title": "Mandy", "french_title": "Mandy", "directors": "Panos"})
    out = apply_filters(df, AgendaFilters(search=needle))
    assert out["_film_key"].tolist() == ["vertigo"]


def test_search_treats_metacharacters_literally():
    df = _frame({"letterboxd_title": "Kill Bill (Vol. 1)"}, {"letterboxd_title": "Vertigo"})
    out = apply_filters(df, AgendaFilters(search="("))
    assert out["letterboxd_title"].tolist() == ["Kill Bill (Vol. 1)"]


def test_only_free_delegates_to_availability_rules():
    df = _frame(
        {"showtimes": "2026-08-05 14:00"},  # Wed afternoon — working day, before cutoff
        {"showtimes": "2026-08-05 20:00"},  # Wed evening — after cutoff
        {"showtimes": "2026-08-08 14:00"},  # Sat afternoon — weekend, free all day
    )
    out = apply_filters(df, AgendaFilters(only_free=True))
    assert out["_dt"].dt.strftime("%Y-%m-%d %H:%M").tolist() == ["2026-08-05 20:00", "2026-08-08 14:00"]


def test_filters_compose():
    df = _frame(
        {"theater_name": "Le Champo", "letterboxd_avg_rating": 4.5},
        {"theater_name": "Le Champo", "letterboxd_avg_rating": 1.0},
        {"theater_name": "MK2 Bibliothèque", "letterboxd_avg_rating": 4.5},
    )
    out = apply_filters(df, AgendaFilters(theaters=("Le Champo",), min_rating=3.0))
    assert len(out) == 1


def test_apply_filters_is_total_when_optional_columns_are_missing():
    """A frame with no theater/rating/director columns narrows on what it has, never raises."""
    df = pd.DataFrame([{"showtimes": "2026-08-04 19:00", "letterboxd_title": "Vertigo"}])
    filters = AgendaFilters(theaters=("Le Champo",), min_rating=4.0, search="vertigo")
    assert len(apply_filters(df, filters)) == 1


def test_search_matches_nothing_when_no_searchable_column_exists():
    df = pd.DataFrame([{"showtimes": "2026-08-04 19:00", "letterboxd_slug": "vertigo"}])
    assert apply_filters(df, AgendaFilters(search="vertigo")).empty


def test_apply_filters_does_not_mutate_input():
    df = _frame({})
    apply_filters(df, AgendaFilters(min_rating=5.0))
    assert len(df) == 1 and "_dt" not in df.columns


def test_active_count_counts_only_non_default_controls():
    assert AgendaFilters().active_count() == 0
    assert AgendaFilters(search="  ").active_count() == 0
    assert AgendaFilters(search="bong", theaters=("Le Champo",), min_rating=3.0).active_count() == 3


# ── apply_day / day_chips ────────────────────────────────────────────────────


def test_apply_day_none_is_identity():
    df = with_agenda_columns(_frame({}, {"showtimes": "2026-08-05 20:00"}))
    assert len(apply_day(df, None)) == 2


def test_apply_day_scopes_to_one_day():
    df = with_agenda_columns(_frame({}, {"showtimes": "2026-08-05 20:00"}))
    assert apply_day(df, dt.date(2026, 8, 5))["_dt"].dt.hour.tolist() == [20]


def test_apply_day_with_absent_day_keeps_columns():
    df = with_agenda_columns(_frame({}))
    out = apply_day(df, dt.date(2026, 12, 25))
    assert out.empty
    assert "_film_key" in out.columns


def test_day_chips_lead_with_all_then_count_screenings():
    df = with_agenda_columns(
        _frame({}, {"showtimes": "2026-08-04 21:30", "letterboxd_slug": "mandy"}, {"showtimes": "2026-08-05 20:00"})
    )
    chips = day_chips(df)
    assert chips[0] == DayChip(day=None, label="All", count=3)
    assert chips[1:] == [
        DayChip(day=dt.date(2026, 8, 4), label="Tue 4", count=2),
        DayChip(day=dt.date(2026, 8, 5), label="Wed 5", count=1),
    ]


def test_day_chips_empty_frame_yields_no_strip():
    assert day_chips(pd.DataFrame()) == []


# ── build_agenda ─────────────────────────────────────────────────────────────


def test_one_entry_per_film_per_day_collecting_showtimes():
    df = _frame({}, {"showtimes": "2026-08-04 21:30", "theater_name": "MK2 Bibliothèque"})
    days = build_agenda(df, today=TODAY)
    assert len(days) == 1
    assert days[0].film_count == 1
    entry = days[0].entries[0]
    assert [s.when.strftime("%H:%M") for s in entry.showtimes] == ["19:00", "21:30"]
    assert [s.theater for s in entry.showtimes] == ["Le Champo", "MK2 Bibliothèque"]


def test_same_film_on_two_days_yields_two_entries():
    days = build_agenda(_frame({}, {"showtimes": "2026-08-05 19:00"}), today=TODAY)
    assert [d.day for d in days] == [dt.date(2026, 8, 4), dt.date(2026, 8, 5)]
    assert [d.film_count for d in days] == [1, 1]


def test_colliding_titles_with_different_slugs_stay_separate():
    """22 real watchlist titles name two different films — keying on the title merges them."""
    days = build_agenda(
        _frame(
            {"letterboxd_slug": "king-lear-1971", "letterboxd_title": "King Lear", "directors": "Peter Brook"},
            {"letterboxd_slug": "king-lear-1987", "letterboxd_title": "King Lear", "directors": "Jean-Luc Godard"},
        ),
        today=TODAY,
    )
    assert days[0].film_count == 2


def test_rows_without_any_identity_are_still_grouped_not_dropped():
    days = build_agenda(
        _frame({"letterboxd_slug": None, "letterboxd_title": None, "french_title": None}),
        today=TODAY,
    )
    assert days[0].film_count == 1


def test_representative_row_carries_the_earliest_showtime():
    df = _frame({"showtimes": "2026-08-04 21:30"}, {"showtimes": "2026-08-04 19:00"})
    entry = build_agenda(df, today=TODAY)[0].entries[0]
    assert entry.earliest == pd.Timestamp("2026-08-04 19:00")
    assert entry.row["_dt"] == pd.Timestamp("2026-08-04 19:00")


def test_sort_time_orders_entries_by_earliest_showtime():
    df = _frame(
        {"letterboxd_slug": "late", "showtimes": "2026-08-04 21:30"},
        {"letterboxd_slug": "early", "showtimes": "2026-08-04 18:00"},
    )
    days = build_agenda(df, sort="time", today=TODAY)
    assert [e.row["_film_key"] for e in days[0].entries] == ["early", "late"]


def test_sort_match_reorders_within_the_day_only():
    df = _frame(
        {"letterboxd_slug": "early-weak", "showtimes": "2026-08-04 18:00", "match": 40.0},
        {"letterboxd_slug": "late-strong", "showtimes": "2026-08-04 21:30", "match": 90.0},
        {"letterboxd_slug": "next-day", "showtimes": "2026-08-05 18:00", "match": 99.0},
    )
    days = build_agenda(df, sort="match", today=TODAY)
    assert [d.day for d in days] == [dt.date(2026, 8, 4), dt.date(2026, 8, 5)]
    assert [e.row["_film_key"] for e in days[0].entries] == ["late-strong", "early-weak"]


def test_sort_match_puts_unscored_entries_last():
    df = _frame(
        {"letterboxd_slug": "unscored", "showtimes": "2026-08-04 18:00", "match": float("nan")},
        {"letterboxd_slug": "scored", "showtimes": "2026-08-04 21:30", "match": 55.0},
    )
    days = build_agenda(df, sort="match", today=TODAY)
    assert [e.row["_film_key"] for e in days[0].entries] == ["scored", "unscored"]


@pytest.mark.parametrize("sort", ["time", "match"])
def test_showtimes_within_an_entry_are_always_chronological(sort):
    df = _frame({"showtimes": "2026-08-04 21:30"}, {"showtimes": "2026-08-04 19:00"})
    entry = build_agenda(df, sort=sort, today=TODAY)[0].entries[0]
    assert [s.when.strftime("%H:%M") for s in entry.showtimes] == ["19:00", "21:30"]


def test_day_labels_and_today_flag():
    days = build_agenda(_frame({}, {"showtimes": "2026-08-07 19:00"}), today=TODAY)
    assert [d.label for d in days] == ["Tonight", "Friday 07 August"]
    assert [d.is_today for d in days] == [True, False]


def test_build_agenda_on_empty_frame():
    assert build_agenda(pd.DataFrame(columns=["showtimes"])) == []


# ── agenda_kpis ──────────────────────────────────────────────────────────────


def test_agenda_kpis_counts_distinct_films_theaters_and_nights():
    df = _frame(
        {},
        {"showtimes": "2026-08-04 21:30"},  # same film + theater, second screening
        {"showtimes": "2026-08-05 20:00", "letterboxd_slug": "mandy", "theater_name": "MK2 Bibliothèque"},
    )
    assert agenda_kpis(df) == [("Films", 2), ("Screenings", 3), ("Theaters", 2), ("Nights", 2)]


def test_agenda_kpis_on_empty_frame_still_returns_four_tuples():
    assert agenda_kpis(pd.DataFrame()) == [("Films", 0), ("Screenings", 0), ("Theaters", 0), ("Nights", 0)]
