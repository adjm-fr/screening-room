"""Unit tests for modules/utils.py."""

import os

import pandas as pd

from modules.utils import build_movies_df, find_stale_slugs, merge_letterboxd_metadata, reorder_columns, save_parquet

# ── build_movies_df ───────────────────────────────────────────────────────────


def test_build_movies_df_ratings_source():
    films = {"movies": {"slug-a": {"rating": 8, "liked": True, "name": "A", "year": 2020}}}
    df = build_movies_df(films, {"data": {}})
    assert list(df["source"]) == ["ratings"]
    assert df.iloc[0]["user_rating"] == 8
    assert df.iloc[0]["liked"] == True  # noqa: E712 — numpy bool, `is` would fail


def test_build_movies_df_watchlist_source():
    watchlist = {"data": {"1": {"slug": "slug-b", "name": "B", "year": 2021}}}
    df = build_movies_df({"movies": {}}, watchlist)
    assert list(df["source"]) == ["watchlist"]


def test_build_movies_df_combined():
    films = {"movies": {"slug-a": {"rating": 7, "liked": False, "name": "A", "year": 2020}}}
    watchlist = {"data": {"1": {"slug": "slug-b", "name": "B", "year": 2021}}}
    df = build_movies_df(films, watchlist)
    assert len(df) == 2
    assert set(df["source"]) == {"ratings", "watchlist"}


def test_build_movies_df_skips_watchlist_entry_without_slug():
    watchlist = {"data": {"1": {"name": "No Slug", "year": 2020}}}
    df = build_movies_df({"movies": {}}, watchlist)
    assert df.empty


def test_build_movies_df_empty_inputs():
    df = build_movies_df({"movies": {}}, {"data": {}})
    assert df.empty


# ── merge_letterboxd_metadata ─────────────────────────────────────────────────


def test_merge_uses_letterboxd_release_year():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2000, "integration_date": "2024-01-01"}])
    result = merge_letterboxd_metadata(movies, cache)
    assert result.iloc[0]["release_year"] == 2000


def test_merge_falls_back_to_user_release_year():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": None, "integration_date": "2024-01-01"}])
    result = merge_letterboxd_metadata(movies, cache)
    assert result.iloc[0]["release_year"] == 1999


def test_merge_drops_name_column():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = merge_letterboxd_metadata(movies, cache)
    assert "name" not in result.columns


def test_merge_drops_integration_date_column():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = merge_letterboxd_metadata(movies, cache)
    assert "integration_date" not in result.columns


def test_merge_preserves_unmatched_slug_with_nulls():
    movies = pd.DataFrame([{"slug": "slug-z", "release_year": 2020, "name": "Z", "source": "watchlist"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = merge_letterboxd_metadata(movies, cache)
    assert len(result) == 1
    assert pd.isna(result.iloc[0]["title"])


# ── reorder_columns ───────────────────────────────────────────────────────────


def test_reorder_columns_prioritises_specified_columns():
    df = pd.DataFrame([{"z": 1, "a": 2, "slug": 3, "title": 4}])
    result = reorder_columns(df, ["slug", "title"])
    assert list(result.columns[:2]) == ["slug", "title"]


def test_reorder_columns_appends_extra_columns():
    df = pd.DataFrame([{"slug": 1, "title": 2, "extra_col": 3}])
    result = reorder_columns(df, ["slug", "title"])
    assert result.columns[-1] == "extra_col"


def test_reorder_columns_skips_missing_columns():
    df = pd.DataFrame([{"slug": 1}])
    result = reorder_columns(df, ["slug", "title", "genres"])
    assert list(result.columns) == ["slug"]


# ── find_stale_slugs ──────────────────────────────────────────────────────────


def test_find_stale_slugs_returns_old_slugs():
    now = pd.to_datetime("2025-01-01")
    df = pd.DataFrame(
        {
            "slug": ["old", "fresh"],
            "integration_date": pd.to_datetime(["2023-01-01", "2024-12-01"]),
        }
    )
    assert find_stale_slugs(df, 365, now) == ["old"]


def test_find_stale_slugs_returns_empty_when_all_fresh():
    now = pd.to_datetime("2025-01-01")
    df = pd.DataFrame(
        {
            "slug": ["fresh"],
            "integration_date": pd.to_datetime(["2024-12-31"]),
        }
    )
    assert find_stale_slugs(df, 365, now) == []


def test_find_stale_slugs_boundary_not_included():
    now = pd.to_datetime("2025-01-01")
    df = pd.DataFrame(
        {
            "slug": ["boundary"],
            # 364 days before now — strictly below threshold, not stale
            "integration_date": pd.to_datetime(["2024-01-02"]),
        }
    )
    assert find_stale_slugs(df, 365, now) == []


# ── save_parquet ──────────────────────────────────────────────────────────────


def test_save_parquet_writes_file(tmp_path):
    df = pd.DataFrame([{"slug": "a", "title": "A", "extra": 1}])
    out = tmp_path / "out.parquet"
    save_parquet(df, ["slug", "title"], out)
    assert out.exists()


def test_save_parquet_applies_column_order(tmp_path):
    df = pd.DataFrame([{"extra": 1, "title": "A", "slug": "a"}])
    out = tmp_path / "out.parquet"
    save_parquet(df, ["slug", "title"], out)
    result = pd.read_parquet(out)
    assert list(result.columns[:2]) == ["slug", "title"]


def test_save_parquet_accepts_string_path(tmp_path):
    df = pd.DataFrame([{"slug": "a"}])
    out = str(tmp_path / "out.parquet")
    save_parquet(df, ["slug"], out)
    assert os.path.exists(out)
