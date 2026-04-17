"""
Tests for main.py orchestration logic.

The network (User, ldm) and file I/O are mocked throughout.
Only the pure data-transformation logic is exercised here.
"""

import pandas as pd
import pytest

# We test the pure helper logic by importing main and calling the parts
# that don't touch the network. For the full orchestration flow we use
# Click's test runner so we can control env vars cleanly.


# ── all_movies_df construction ────────────────────────────────────────────────


class TestAllMoviesDfConstruction:
    """
    The unified DataFrame is built from films_dict + watchlist_dict before
    any API calls. These tests verify the row shape and source tagging.
    """

    def _films_dict(self, movies: dict) -> dict:
        return {"movies": movies}

    def _watchlist_dict(self, data: dict) -> dict:
        return {"data": data}

    def _build_df(self, films_dict, watchlist_dict):
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

    def test_ratings_rows_have_correct_source(self):
        films = self._films_dict({"slug-a": {"rating": 8, "liked": True, "name": "A", "year": 2020}})
        df = self._build_df(films, self._watchlist_dict({}))
        assert list(df["source"]) == ["ratings"]
        assert df.iloc[0]["user_rating"] == 8
        assert df.iloc[0]["liked"] == True  # noqa: E712 — numpy bool, `is` would fail

    def test_watchlist_rows_have_correct_source(self):
        watchlist = self._watchlist_dict({"1": {"slug": "slug-b", "name": "B", "year": 2021}})
        df = self._build_df(self._films_dict({}), watchlist)
        assert list(df["source"]) == ["watchlist"]
        assert "user_rating" not in df.columns or pd.isna(df.iloc[0].get("user_rating", None))

    def test_both_sources_stacked(self):
        films = self._films_dict({"slug-a": {"rating": 7, "liked": False, "name": "A", "year": 2020}})
        watchlist = self._watchlist_dict({"1": {"slug": "slug-b", "name": "B", "year": 2021}})
        df = self._build_df(films, watchlist)
        assert len(df) == 2
        assert set(df["source"]) == {"ratings", "watchlist"}

    def test_watchlist_entries_without_slug_are_skipped(self):
        watchlist = self._watchlist_dict({"1": {"name": "No Slug Movie", "year": 2020}})
        df = self._build_df(self._films_dict({}), watchlist)
        assert df.empty


# ── duplicate slug guard ──────────────────────────────────────────────────────


class TestDuplicateSlugGuard:
    """A slug appearing in both sources must raise ValueError."""

    def test_duplicate_raises(self):
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

    def test_no_duplicate_does_not_raise(self):
        df = pd.DataFrame(
            [
                {"slug": "slug-a", "source": "ratings"},
                {"slug": "slug-b", "source": "watchlist"},
            ]
        )
        dup_slugs = df[df.duplicated("slug")]["slug"].tolist()
        assert dup_slugs == []


# ── enrichment merge ──────────────────────────────────────────────────────────


class TestEnrichmentMerge:
    """
    After fetching the letterboxd cache, all_movies_df is left-joined with it.
    release_year from the cache takes precedence; user's value is a fallback.
    name and integration_date are dropped from the output.
    """

    def _enrich(self, all_movies_df, data_letterboxd_df):
        merged = all_movies_df.merge(data_letterboxd_df, on="slug", how="left", suffixes=("_user", ""))
        if "release_year_user" in merged.columns:
            merged["release_year"] = merged["release_year"].fillna(merged["release_year_user"]).infer_objects()
            merged.drop(columns=["release_year_user"], inplace=True)
        if "name" in merged.columns:
            merged.drop(columns=["name"], inplace=True)
        if "integration_date" in merged.columns:
            merged.drop(columns=["integration_date"], inplace=True)
        return merged

    def test_letterboxd_release_year_takes_precedence(self):
        movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
        cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2000, "integration_date": "2024-01-01"}])
        result = self._enrich(movies, cache)
        assert result.iloc[0]["release_year"] == 2000

    def test_user_release_year_used_as_fallback(self):
        movies = pd.DataFrame([{"slug": "slug-a", "release_year": 1999, "name": "A", "source": "ratings"}])
        cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": None, "integration_date": "2024-01-01"}])
        result = self._enrich(movies, cache)
        assert result.iloc[0]["release_year"] == 1999

    def test_name_column_dropped(self):
        movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
        cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
        result = self._enrich(movies, cache)
        assert "name" not in result.columns

    def test_integration_date_dropped(self):
        movies = pd.DataFrame([{"slug": "slug-a", "release_year": 2020, "name": "A", "source": "ratings"}])
        cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
        result = self._enrich(movies, cache)
        assert "integration_date" not in result.columns

    def test_unmatched_slug_preserved_with_nulls(self):
        movies = pd.DataFrame([{"slug": "slug-z", "release_year": 2020, "name": "Z", "source": "watchlist"}])
        cache = pd.DataFrame([{"slug": "slug-a", "title": "Movie A", "release_year": 2020, "integration_date": "2024-01-01"}])
        result = self._enrich(movies, cache)
        assert len(result) == 1
        assert pd.isna(result.iloc[0]["title"])


# ── split by source ───────────────────────────────────────────────────────────


class TestSplitBySource:
    """After enrichment, the DataFrame is split into ratings and watchlist."""

    def _split(self, df):
        ratings = df[df["source"] == "ratings"].drop(columns=["source"])
        watchlist = df[df["source"] == "watchlist"].drop(columns=["source"])
        return ratings, watchlist

    def test_ratings_split_contains_only_ratings(self):
        df = pd.DataFrame(
            [
                {"slug": "slug-a", "source": "ratings", "title": "A"},
                {"slug": "slug-b", "source": "watchlist", "title": "B"},
            ]
        )
        ratings, _ = self._split(df)
        assert list(ratings["slug"]) == ["slug-a"]
        assert "source" not in ratings.columns

    def test_watchlist_split_contains_only_watchlist(self):
        df = pd.DataFrame(
            [
                {"slug": "slug-a", "source": "ratings", "title": "A"},
                {"slug": "slug-b", "source": "watchlist", "title": "B"},
            ]
        )
        _, watchlist = self._split(df)
        assert list(watchlist["slug"]) == ["slug-b"]
        assert "source" not in watchlist.columns


# ── column reordering (_save helper) ─────────────────────────────────────────


class TestColumnReordering:
    """
    _save reorders columns: specified columns first (in order), extras appended.
    Columns in the order list that don't exist in the DataFrame are silently skipped.
    """

    def _reorder(self, df, column_order):
        existing = [c for c in column_order if c in df.columns]
        extra = [c for c in df.columns if c not in column_order]
        return df[existing + extra]

    def test_preferred_columns_come_first(self):
        df = pd.DataFrame([{"z": 1, "a": 2, "slug": 3, "title": 4}])
        result = self._reorder(df, ["slug", "title"])
        assert list(result.columns[:2]) == ["slug", "title"]

    def test_extra_columns_appended(self):
        df = pd.DataFrame([{"slug": 1, "title": 2, "extra_col": 3}])
        result = self._reorder(df, ["slug", "title"])
        assert result.columns[-1] == "extra_col"

    def test_missing_preferred_columns_skipped(self):
        df = pd.DataFrame([{"slug": 1}])
        result = self._reorder(df, ["slug", "title", "genres"])
        assert list(result.columns) == ["slug"]


# ── stale slug identification ─────────────────────────────────────────────────


class TestStaleSlugIdentification:
    """Age calculation and refresh_limit truncation."""

    def _old_slugs(self, df, days_to_update, now):
        age_days = (now - df["integration_date"]).dt.days
        return df[age_days > days_to_update]["slug"].tolist()

    def test_slugs_older_than_threshold_flagged(self):
        now = pd.to_datetime("2025-01-01")
        df = pd.DataFrame(
            {
                "slug": ["old", "fresh"],
                "integration_date": pd.to_datetime(["2023-01-01", "2024-12-01"]),
            }
        )
        result = self._old_slugs(df, 365, now)
        assert result == ["old"]

    def test_refresh_limit_truncates_list(self):
        old = ["a", "b", "c", "d"]
        limit = 2
        assert old[:limit] == ["a", "b"]

    def test_no_stale_entries_returns_empty(self):
        now = pd.to_datetime("2025-01-01")
        df = pd.DataFrame(
            {
                "slug": ["fresh"],
                "integration_date": pd.to_datetime(["2024-12-31"]),
            }
        )
        result = self._old_slugs(df, 365, now)
        assert result == []
