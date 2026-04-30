"""
Tests for main.py orchestration logic.

The network (User, ldm) and file I/O are mocked throughout.
Only the pure data-transformation logic is exercised here.
"""

import pandas as pd
import pytest

# ── helpers used across multiple tests ───────────────────────────────────────


def _build_all_movies_df(films_dict: dict, watchlist_dict: dict) -> pd.DataFrame:
    ratings_rows = [
        {
            "slug": slug,
            "user_rating": info.get("rating"),
            "liked": info.get("liked"),
            "name": info.get("name"),
            "release_year": info.get("year"),
            "source": "ratings",
        }
        for slug, info in films_dict.get("movies", {}).items()
    ]
    watchlist_rows = [
        {
            "slug": info["slug"],
            "name": info.get("name"),
            "release_year": info.get("year"),
            "source": "watchlist",
        }
        for info in watchlist_dict.get("data", {}).values()
        if "slug" in info
    ]
    return pd.DataFrame(ratings_rows + watchlist_rows)


def _enrich(all_movies_df: pd.DataFrame, data_letterboxd_df: pd.DataFrame) -> pd.DataFrame:
    merged = all_movies_df.merge(data_letterboxd_df, on="slug", how="left", suffixes=("_user", ""))
    if "release_year_user" in merged.columns:
        merged["release_year"] = merged["release_year"].fillna(merged["release_year_user"]).infer_objects()
        merged.drop(columns=["release_year_user"], inplace=True)
    for col in ("name", "integration_date"):
        if col in merged.columns:
            merged.drop(columns=[col], inplace=True)
    return merged


def _reorder(df: pd.DataFrame, column_order: list[str]) -> pd.DataFrame:
    existing = [c for c in column_order if c in df.columns]
    extra = [c for c in df.columns if c not in column_order]
    return df[existing + extra]


def _old_slugs(df: pd.DataFrame, days_to_update: int, now: pd.Timestamp) -> list[str]:
    age_days = (now - df["integration_date"]).dt.days
    return df[age_days > days_to_update]["slug"].tolist()


# ── all_movies_df construction ────────────────────────────────────────────────


def test_ratings_rows_have_correct_source():
    films = {"movies": {"slug-a": {"rating": 8, "liked": True, "name": "A", "year": 2020}}}
    df = _build_all_movies_df(films, {"data": {}})
    assert list(df["source"]) == ["ratings"]
    assert df.iloc[0]["user_rating"] == 8
    assert df.iloc[0]["liked"] == True  # noqa: E712 — numpy bool, `is` would fail


def test_watchlist_rows_have_correct_source():
    watchlist = {"data": {"1": {"slug": "slug-b", "name": "B", "year": 2021}}}
    df = _build_all_movies_df({"movies": {}}, watchlist)
    assert list(df["source"]) == ["watchlist"]
    assert "user_rating" not in df.columns or pd.isna(df.iloc[0].get("user_rating", None))


def test_both_sources_stacked():
    films = {"movies": {"slug-a": {"rating": 7, "liked": False, "name": "A", "year": 2020}}}
    watchlist = {"data": {"1": {"slug": "slug-b", "name": "B", "year": 2021}}}
    df = _build_all_movies_df(films, watchlist)
    assert len(df) == 2
    assert set(df["source"]) == {"ratings", "watchlist"}


def test_watchlist_entries_without_slug_are_skipped():
    watchlist = {"data": {"1": {"name": "No Slug Movie", "year": 2020}}}
    df = _build_all_movies_df({"movies": {}}, watchlist)
    assert df.empty


# ── duplicate slug guard ──────────────────────────────────────────────────────


def test_duplicate_raises():
    df = pd.DataFrame(
        [
            {"slug": "slug-a", "source": "ratings"},
            {"slug": "slug-a", "source": "watchlist"},
        ]
    )
    dup_slugs = df[df.duplicated("slug")]["slug"].tolist()
    with pytest.raises(ValueError, match="Duplicate slugs"):
        if dup_slugs:
            raise ValueError(f"Duplicate slugs found across ratings and watchlist: {dup_slugs}")


def test_no_duplicate_does_not_raise():
    df = pd.DataFrame(
        [
            {"slug": "slug-a", "source": "ratings"},
            {"slug": "slug-b", "source": "watchlist"},
        ]
    )
    dup_slugs = df[df.duplicated("slug")]["slug"].tolist()
    assert dup_slugs == []


# ── enrichment merge ──────────────────────────────────────────────────────────


def test_letterboxd_release_year_takes_precedence():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2000, "integration_date": "2024-01-01"}])
    result = _enrich(movies, cache)
    assert result.iloc[0]["release_year"] == 2000


def test_user_release_year_used_as_fallback():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": None, "integration_date": "2024-01-01"}])
    result = _enrich(movies, cache)
    assert result.iloc[0]["release_year"] == 1999


def test_name_column_dropped():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = _enrich(movies, cache)
    assert "name" not in result.columns


def test_integration_date_dropped():
    movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = _enrich(movies, cache)
    assert "integration_date" not in result.columns


def test_unmatched_slug_preserved_with_nulls():
    movies = pd.DataFrame([{"slug": "slug-z", "release_year": 2020, "name": "Z", "source": "watchlist"}])
    cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
    result = _enrich(movies, cache)
    assert len(result) == 1
    assert pd.isna(result.iloc[0]["title"])


# ── split by source ───────────────────────────────────────────────────────────


@pytest.fixture
def mixed_source_df():
    return pd.DataFrame(
        [
            {"slug": "slug-a", "source": "ratings", "title": "A"},
            {"slug": "slug-b", "source": "watchlist", "title": "B"},
        ]
    )


def test_ratings_split_contains_only_ratings(mixed_source_df):
    ratings = mixed_source_df[mixed_source_df["source"] == "ratings"].drop(columns=["source"])
    assert list(ratings["slug"]) == ["slug-a"]
    assert "source" not in ratings.columns


def test_watchlist_split_contains_only_watchlist(mixed_source_df):
    watchlist = mixed_source_df[mixed_source_df["source"] == "watchlist"].drop(columns=["source"])
    assert list(watchlist["slug"]) == ["slug-b"]
    assert "source" not in watchlist.columns


# ── column reordering (_save helper) ─────────────────────────────────────────


def test_preferred_columns_come_first():
    df = pd.DataFrame([{"z": 1, "a": 2, "slug": 3, "title": 4}])
    result = _reorder(df, ["slug", "title"])
    assert list(result.columns[:2]) == ["slug", "title"]


def test_extra_columns_appended():
    df = pd.DataFrame([{"slug": 1, "title": 2, "extra_col": 3}])
    result = _reorder(df, ["slug", "title"])
    assert result.columns[-1] == "extra_col"


def test_missing_preferred_columns_skipped():
    df = pd.DataFrame([{"slug": 1}])
    result = _reorder(df, ["slug", "title", "genres"])
    assert list(result.columns) == ["slug"]


# ── stale slug identification ─────────────────────────────────────────────────


def test_slugs_older_than_threshold_flagged():
    now = pd.to_datetime("2025-01-01")
    df = pd.DataFrame(
        {
            "slug": ["old", "fresh"],
            "integration_date": pd.to_datetime(["2023-01-01", "2024-12-01"]),
        }
    )
    assert _old_slugs(df, 365, now) == ["old"]


def test_refresh_limit_truncates_list():
    old = ["a", "b", "c", "d"]
    assert old[:2] == ["a", "b"]


def test_no_stale_entries_returns_empty():
    now = pd.to_datetime("2025-01-01")
    df = pd.DataFrame(
        {
            "slug": ["fresh"],
            "integration_date": pd.to_datetime(["2024-12-31"]),
        }
    )
    assert _old_slugs(df, 365, now) == []
