"""
Tests for letterboxd_data_management/get_letterboxd_data.py.

All tests are offline — no real API calls are made.
_fetch_movie and parquet I/O are mocked where needed.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from letterboxd_data_management.get_letterboxd_data import (
    _fetch_movie,
    get_letterboxd_data,
    refresh_letterboxd_data,
)


# ── _fetch_movie ──────────────────────────────────────────────────────────────

class TestFetchMovieGenreFiltering:
    """Genre/theme/mini-theme splitting from the raw genres list."""

    def _make_movie(self, genres=None, details=None, crew=None):
        m = MagicMock()
        m.genres = genres or []
        m.details = details or []
        m.crew = crew or {}
        m.id = "id"
        m.url = "url"
        m.imdb_id = None
        m.tmdb_id = None
        m.imdb_link = None
        m.tmdb_link = None
        m.title = "Title"
        m.original_title = None
        m.year = 2020
        m.runtime = 90
        m.tagline = None
        m.description = None
        m.rating = 7.5
        m.poster = None
        m.banner = None
        return m

    def test_genres_split_by_type(self):
        genres = [
            {"type": "genre", "name": "Drama"},
            {"type": "genre", "name": "Thriller"},
            {"type": "theme", "name": "Revenge"},
            {"type": "mini-theme", "name": "Heist"},
        ]
        with patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=self._make_movie(genres=genres)):
            result = _fetch_movie("some-slug")
        assert result["genres"] == "Drama, Thriller"
        assert result["themes"] == "Revenge"
        assert result["mini_themes"] == "Heist"

    def test_empty_genres_returns_none(self):
        with patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=self._make_movie()):
            result = _fetch_movie("some-slug")
        assert result["genres"] is None
        assert result["themes"] is None
        assert result["mini_themes"] is None

    def test_details_grouped_by_type(self):
        details = [
            {"type": "studio", "name": "A24"},
            {"type": "country", "name": "USA"},
            {"type": "country", "name": "UK"},
        ]
        with patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=self._make_movie(details=details)):
            result = _fetch_movie("some-slug")
        assert result["studio"] == "A24"
        assert result["country"] == "USA, UK"

    def test_crew_filtered_to_director_producer_writer(self):
        crew = {
            "director": [{"name": "Jane Doe"}],
            "producer": [{"name": "John Smith"}, {"name": "Alice"}],
            "writer": [],
            "editor": [{"name": "Bob"}],  # should be excluded
        }
        with patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=self._make_movie(crew=crew)):
            result = _fetch_movie("some-slug")
        assert result["directors"] == "Jane Doe"
        assert result["producers"] == "John Smith, Alice"
        assert result["writers"] is None
        assert "editor" not in result

    def test_exception_returns_none(self):
        with patch("letterboxd_data_management.get_letterboxd_data.Movie", side_effect=Exception("network error")):
            result = _fetch_movie("bad-slug")
        assert result is None


# ── get_letterboxd_data ───────────────────────────────────────────────────────

class TestGetLetterboxdData:
    """Cache delta logic and DataFrame construction."""

    def _make_cache(self, slugs: list[str]) -> pd.DataFrame:
        return pd.DataFrame({
            "slug": slugs,
            "title": [f"Movie {s}" for s in slugs],
            "integration_date": pd.to_datetime(date(2024, 1, 1)),
        })

    def test_no_new_slugs_returns_cache_unchanged(self, tmp_path):
        cache = self._make_cache(["slug-a", "slug-b"])
        cache_path = str(tmp_path / "cache.parquet")
        cache.to_parquet(cache_path, index=False)

        result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

        assert set(result["slug"]) == {"slug-a", "slug-b"}

    def test_new_slugs_are_fetched_and_appended(self, tmp_path):
        cache = self._make_cache(["slug-a"])
        cache_path = str(tmp_path / "cache.parquet")
        cache.to_parquet(cache_path, index=False)

        fake_result = {"slug": "slug-b", "title": "Movie B", "release_year": 2020}
        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fake_result):
            result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

        assert set(result["slug"]) == {"slug-a", "slug-b"}

    def test_integration_date_set_to_today_for_new_slugs(self, tmp_path):
        cache_path = str(tmp_path / "cache.parquet")
        fake_result = {"slug": "slug-a", "title": "Movie A", "release_year": 2020}

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fake_result):
            result = get_letterboxd_data(["slug-a"], cache_path)

        today = pd.to_datetime(date.today())
        assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today

    def test_failed_fetch_is_skipped_gracefully(self, tmp_path):
        cache_path = str(tmp_path / "cache.parquet")

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=None):
            result = get_letterboxd_data(["bad-slug"], cache_path)

        assert result.empty

    def test_no_cache_file_starts_fresh(self, tmp_path):
        cache_path = str(tmp_path / "nonexistent.parquet")
        fake_result = {"slug": "slug-a", "title": "Movie A", "release_year": 2020}

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fake_result):
            result = get_letterboxd_data(["slug-a"], cache_path)

        assert len(result) == 1
        assert result.iloc[0]["slug"] == "slug-a"


# ── refresh_letterboxd_data ───────────────────────────────────────────────────

class TestRefreshLetterboxdData:
    """Index-based update logic and integration_date refresh."""

    def _make_df(self, rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        df["integration_date"] = pd.to_datetime(date(2023, 1, 1))
        return df

    def test_empty_refresh_list_returns_df_unchanged(self, tmp_path):
        df = self._make_df([{"slug": "slug-a", "title": "Old Title"}])
        result = refresh_letterboxd_data(df, [], str(tmp_path / "cache.parquet"), {})
        pd.testing.assert_frame_equal(result, df)

    def test_refreshed_slug_gets_updated_fields(self, tmp_path):
        df = self._make_df([
            {"slug": "slug-a", "title": "Old Title"},
            {"slug": "slug-b", "title": "Untouched"},
        ])
        cache_path = str(tmp_path / "cache.parquet")
        fresh = {"slug": "slug-a", "title": "New Title"}

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fresh):
            result = refresh_letterboxd_data(df, ["slug-a"], cache_path, {})

        assert result.loc[result["slug"] == "slug-a", "title"].iloc[0] == "New Title"

    def test_non_refreshed_slug_is_preserved(self, tmp_path):
        df = self._make_df([
            {"slug": "slug-a", "title": "Old Title"},
            {"slug": "slug-b", "title": "Untouched"},
        ])
        cache_path = str(tmp_path / "cache.parquet")
        fresh = {"slug": "slug-a", "title": "New Title"}

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fresh):
            result = refresh_letterboxd_data(df, ["slug-a"], cache_path, {})

        assert result.loc[result["slug"] == "slug-b", "title"].iloc[0] == "Untouched"

    def test_integration_date_updated_on_refresh(self, tmp_path):
        df = self._make_df([{"slug": "slug-a", "title": "Movie A"}])
        cache_path = str(tmp_path / "cache.parquet")
        fresh = {"slug": "slug-a", "title": "Movie A"}

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=fresh):
            result = refresh_letterboxd_data(df, ["slug-a"], cache_path, {})

        today = pd.to_datetime(date.today())
        assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today

    def test_failed_refresh_leaves_existing_data_intact(self, tmp_path):
        df = self._make_df([{"slug": "slug-a", "title": "Old Title"}])
        cache_path = str(tmp_path / "cache.parquet")

        with patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=None):
            result = refresh_letterboxd_data(df, ["slug-a"], cache_path, {})

        assert result.loc[result["slug"] == "slug-a", "title"].iloc[0] == "Old Title"
