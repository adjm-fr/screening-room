"""Tests for core.library — the Movies Database page's pure statistics layer."""

import pandas as pd
import pytest

from core.agenda import RUNTIME_BUCKETS
from core.library import (
    HALF_STARS,
    RATING_TIERS,
    TABLE_PRESETS,
    decade_profile,
    delta_summary,
    explode_tags,
    filter_table,
    genre_counts,
    preset_columns,
    rating_disagreements,
    rating_histogram,
    runtime_bucket_counts,
)

# ── Tier ladder ─────────────────────────────────────────────────────────────


def test_tiers_cover_every_half_star_exactly_once():
    for star in HALF_STARS:
        covering = [label for lo, hi, label in RATING_TIERS if lo <= star <= hi]
        assert len(covering) == 1, f"{star} covered by {covering}"


def test_tiers_are_ascending_and_disjoint():
    bounds = [b for lo, hi, _ in RATING_TIERS for b in (lo, hi)]
    assert bounds == sorted(bounds)


# ── rating_histogram ────────────────────────────────────────────────────────


def test_histogram_has_all_ten_bins_zero_filled(make_ratings):
    df = make_ratings([{"user_rating": 3.0}, {"user_rating": 3.0}, {"user_rating": 0.5}])
    hist = rating_histogram(df)
    assert list(hist["rating"]) == list(HALF_STARS)
    assert hist.loc[hist["rating"] == 3.0, "count"].item() == 2
    assert hist.loc[hist["rating"] == 0.5, "count"].item() == 1
    assert hist.loc[hist["rating"] == 5.0, "count"].item() == 0


def test_histogram_without_rating_column_is_all_zero():
    hist = rating_histogram(pd.DataFrame({"title": ["x"]}))
    assert list(hist["rating"]) == list(HALF_STARS)
    assert hist["count"].sum() == 0


def test_histogram_ignores_null_ratings(make_ratings):
    df = make_ratings([{"user_rating": None}, {"user_rating": 4.5}])
    hist = rating_histogram(df)
    assert hist["count"].sum() == 1


# ── rating_disagreements / delta_summary ────────────────────────────────────


@pytest.fixture
def comparable_ratings(make_ratings):
    return make_ratings(
        [
            {"slug": "loved", "user_rating": 5.0, "letterboxd_avg_rating": 2.5},
            {"slug": "hated", "user_rating": 0.5, "letterboxd_avg_rating": 4.5},
            {"slug": "agree", "user_rating": 3.0, "letterboxd_avg_rating": 3.0},
            {"slug": "no-community", "user_rating": 4.0, "letterboxd_avg_rating": None},
        ]
    )


def test_disagreements_signs_and_directions(comparable_ratings):
    out = rating_disagreements(comparable_ratings, n=1)
    assert list(out["slug"]) == ["loved", "hated"]
    assert list(out["direction"]) == ["higher", "lower"]
    assert out.loc[0, "delta"] == 2.5
    assert out.loc[1, "delta"] == -4.0


def test_disagreements_drop_rows_missing_either_side(comparable_ratings):
    out = rating_disagreements(comparable_ratings, n=10)
    assert "no-community" not in set(out["slug"])


def test_disagreements_small_frame_has_no_duplicate_rows(comparable_ratings):
    out = rating_disagreements(comparable_ratings, n=10)
    assert len(out) == 3
    assert out["slug"].is_unique


def test_disagreements_without_columns_returns_empty():
    out = rating_disagreements(pd.DataFrame({"title": ["x"]}))
    assert out.empty
    assert "delta" in out.columns


def test_delta_summary(comparable_ratings):
    summary = delta_summary(comparable_ratings)
    assert summary["n"] == 3
    assert summary["mean_delta"] == pytest.approx((2.5 - 4.0 + 0.0) / 3)
    assert summary["share_below"] == pytest.approx(1 / 3)


def test_delta_summary_empty_is_guardable():
    assert delta_summary(pd.DataFrame({"title": ["x"]}))["n"] == 0


# ── decade_profile ──────────────────────────────────────────────────────────


def test_decade_profile_buckets_and_sorts(make_ratings):
    df = make_ratings(
        [
            {"release_year": 1999, "user_rating": 4.0},
            {"release_year": 1990, "user_rating": 2.0},
            {"release_year": 2000, "user_rating": 3.0},
            {"release_year": None, "user_rating": 5.0},
        ]
    )
    out = decade_profile(df)
    assert list(out["decade"]) == [1990, 2000]
    assert out.loc[out["decade"] == 1990, "count"].item() == 2
    assert out.loc[out["decade"] == 1990, "mean_rating"].item() == 3.0


def test_decade_profile_without_years_is_empty():
    out = decade_profile(pd.DataFrame({"user_rating": [3.0]}))
    assert out.empty
    assert list(out.columns) == ["decade", "count", "mean_rating"]


# ── explode_tags ────────────────────────────────────────────────────────────


def test_explode_tags_splits_trims_and_drops_empties():
    tags = explode_tags(pd.Series(["Drama, Comedy", None, " Horror, ", ""]))
    assert list(tags) == ["Drama", "Comedy", "Horror"]


# ── filter_table / presets ──────────────────────────────────────────────────


@pytest.fixture
def table_df():
    return pd.DataFrame(
        {
            "title": ["The Nice Guys", "Ran", "M"],
            "french_title": [None, None, "M le maudit"],
            "directors": ["Shane Black", "Akira Kurosawa", "Fritz Lang"],
            "release_year": [2016, 1985, 1931],
        }
    )


def test_filter_table_is_case_insensitive_across_columns(table_df):
    assert list(filter_table(table_df, "kurosawa")["title"]) == ["Ran"]
    assert list(filter_table(table_df, "maudit")["title"]) == ["M"]


def test_filter_table_blank_query_passes_through(table_df):
    assert filter_table(table_df, "   ") is table_df


def test_filter_table_treats_query_literally(table_df):
    assert filter_table(table_df, "guys (").empty


def test_preset_columns_intersects_with_present(table_df):
    cols = preset_columns(table_df, "Essentials")
    assert cols == ["title", "directors", "release_year"]


def test_preset_columns_all_and_unknown_yield_everything(table_df):
    assert preset_columns(table_df, "All") == list(table_df.columns)
    assert preset_columns(table_df, "nope") == list(table_df.columns)


def test_preset_columns_never_returns_empty():
    df = pd.DataFrame({"only": [1]})
    assert preset_columns(df, "Links") == ["only"]


def test_every_preset_is_a_tuple():
    assert all(isinstance(cols, tuple) for cols in TABLE_PRESETS.values())


# ── runtime_bucket_counts ────────────────────────────────────────────────────


def test_runtime_bucket_counts_all_buckets_zero_filled(make_ratings):
    df = make_ratings([{"runtime": 80}, {"runtime": 100}, {"runtime": 150}, {"runtime": 200}])
    out = runtime_bucket_counts(df)
    assert list(out["bucket"]) == list(RUNTIME_BUCKETS)
    assert out.loc[out["bucket"] == "<90", "count"].item() == 1
    assert out.loc[out["bucket"] == "90–120", "count"].item() == 1
    assert out.loc[out["bucket"] == ">120", "count"].item() == 2


def test_runtime_bucket_counts_without_column_is_zero_filled():
    out = runtime_bucket_counts(pd.DataFrame({"title": ["x"]}))
    assert list(out["bucket"]) == list(RUNTIME_BUCKETS)
    assert out["count"].sum() == 0


def test_runtime_bucket_counts_ignores_unknown_runtimes(make_ratings):
    df = make_ratings([{"runtime": None}, {"runtime": 100}])
    out = runtime_bucket_counts(df)
    assert out["count"].sum() == 1


# ── genre_counts ─────────────────────────────────────────────────────────────


def test_genre_counts_most_frequent_first(make_ratings):
    df = make_ratings([{"genres": "Drama, Comedy"}, {"genres": "Drama"}, {"genres": "Drama, Horror"}, {"genres": "Comedy"}])
    out = genre_counts(df)
    assert list(out["genre"]) == ["Drama", "Comedy", "Horror"]
    assert out.loc[out["genre"] == "Drama", "count"].item() == 3


def test_genre_counts_respects_n(make_ratings):
    df = make_ratings([{"genres": "A, B, C"}])
    assert len(genre_counts(df, n=2)) == 2


def test_genre_counts_without_column_is_empty():
    out = genre_counts(pd.DataFrame({"title": ["x"]}))
    assert out.empty
    assert list(out.columns) == ["genre", "count"]
