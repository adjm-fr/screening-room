"""
Tests for letterboxd_data_management/get_letterboxd_data.py.

All tests are offline — no real API calls are made.
_fetch_movie and parquet I/O are mocked where needed.
"""

from datetime import date

import pandas as pd
import pytest

from letterboxd_data_management.get_letterboxd_data import (
    _fetch_french_title,
    _fetch_movie,
    get_letterboxd_data,
    refresh_letterboxd_data,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cache_df():
    return pd.DataFrame(
        {
            "slug": ["slug-a", "slug-b"],
            "title": ["Movie A", "Movie B"],
            "integration_date": pd.to_datetime(date(2024, 1, 1)),
        }
    )


# ── _fetch_movie ──────────────────────────────────────────────────────────────


def test_genres_split_by_type(mocker, make_movie):
    genres = [
        {"type": "genre", "name": "Drama"},
        {"type": "genre", "name": "Thriller"},
        {"type": "theme", "name": "Revenge"},
        {"type": "mini-theme", "name": "Heist"},
    ]
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=make_movie(genres=genres))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["genres"] == "Drama, Thriller"
    assert result["themes"] == "Revenge"
    assert result["mini_themes"] == "Heist"


def test_empty_genres_returns_none(mocker, make_movie):
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=make_movie())
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["genres"] is None
    assert result["themes"] is None
    assert result["mini_themes"] is None


def test_details_grouped_by_type(mocker, make_movie):
    details = [
        {"type": "studio", "name": "A24"},
        {"type": "country", "name": "USA"},
        {"type": "country", "name": "UK"},
    ]
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=make_movie(details=details))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["studio"] == "A24"
    assert result["country"] == "USA, UK"


def test_crew_filtered_to_director_producer_writer(mocker, make_movie):
    crew = {
        "director": [{"name": "Jane Doe"}],
        "producer": [{"name": "John Smith"}, {"name": "Alice"}],
        "writer": [],
        "editor": [{"name": "Bob"}],  # should be excluded
    }
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=make_movie(crew=crew))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["directors"] == "Jane Doe"
    assert result["producers"] == "John Smith, Alice"
    assert result["writers"] is None
    assert "editor" not in result


def test_exception_returns_none(mocker):
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", side_effect=Exception("network error"))
    result = _fetch_movie("bad-slug")
    assert result is None


# ── get_letterboxd_data ───────────────────────────────────────────────────────


def test_no_new_slugs_returns_cache_unchanged(tmp_path, cache_df):
    cache_path = str(tmp_path / "cache.parquet")
    cache_df.to_parquet(cache_path, index=False)

    result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

    assert set(result["slug"]) == {"slug-a", "slug-b"}


def test_new_slugs_are_fetched_and_appended(tmp_path, cache_df, mocker):
    single_slug_cache = cache_df[cache_df["slug"] == "slug-a"].copy()
    cache_path = str(tmp_path / "cache.parquet")
    single_slug_cache.to_parquet(cache_path, index=False)

    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-b", "title": "Movie B", "release_year": 2020},
    )
    result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

    assert set(result["slug"]) == {"slug-a", "slug-b"}


def test_integration_date_set_to_today_for_new_slugs(tmp_path, mocker):
    cache_path = str(tmp_path / "cache.parquet")
    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "Movie A", "release_year": 2020},
    )
    result = get_letterboxd_data(["slug-a"], cache_path)

    today = pd.to_datetime(date.today())
    assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today


def test_failed_fetch_is_skipped_gracefully(tmp_path, mocker):
    cache_path = str(tmp_path / "cache.parquet")
    mocker.patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=None)
    result = get_letterboxd_data(["bad-slug"], cache_path)

    assert result.empty


def test_no_cache_file_starts_fresh(tmp_path, mocker):
    cache_path = str(tmp_path / "nonexistent.parquet")
    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "Movie A", "release_year": 2020},
    )
    result = get_letterboxd_data(["slug-a"], cache_path)

    assert len(result) == 1
    assert result.iloc[0]["slug"] == "slug-a"


# ── refresh_letterboxd_data ───────────────────────────────────────────────────


@pytest.fixture
def refresh_df():
    df = pd.DataFrame([{"slug": "slug-a", "title": "Old Title"}, {"slug": "slug-b", "title": "Untouched"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))
    return df


def test_empty_refresh_list_returns_df_unchanged(tmp_path, refresh_df):
    result = refresh_letterboxd_data(refresh_df, [], str(tmp_path / "cache.parquet"), {})
    pd.testing.assert_frame_equal(result, refresh_df)


def test_refreshed_slug_gets_updated_fields(tmp_path, refresh_df, mocker):
    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title"},
    )
    result = refresh_letterboxd_data(refresh_df, ["slug-a"], str(tmp_path / "cache.parquet"), {})

    assert result.loc[result["slug"] == "slug-a", "title"].iloc[0] == "New Title"


def test_non_refreshed_slug_is_preserved(tmp_path, refresh_df, mocker):
    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title"},
    )
    result = refresh_letterboxd_data(refresh_df, ["slug-a"], str(tmp_path / "cache.parquet"), {})

    assert result.loc[result["slug"] == "slug-b", "title"].iloc[0] == "Untouched"


def test_integration_date_updated_on_refresh(tmp_path, mocker):
    df = pd.DataFrame([{"slug": "slug-a", "title": "Movie A"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))

    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "Movie A"},
    )
    result = refresh_letterboxd_data(df, ["slug-a"], str(tmp_path / "cache.parquet"), {})

    today = pd.to_datetime(date.today())
    assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today


def test_failed_refresh_leaves_existing_data_intact(tmp_path, mocker):
    df = pd.DataFrame([{"slug": "slug-a", "title": "Old Title"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))

    mocker.patch("letterboxd_data_management.get_letterboxd_data._fetch_movie", return_value=None)
    result = refresh_letterboxd_data(df, ["slug-a"], str(tmp_path / "cache.parquet"), {})

    assert result.loc[result["slug"] == "slug-a", "title"].iloc[0] == "Old Title"


# ── _fetch_french_title ───────────────────────────────────────────────────────


def test_fetch_french_title_returns_title_on_success(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"title": "Le Syndicat du Crime"}
    mocker.patch("letterboxd_data_management.get_letterboxd_data.requests.get", return_value=mock_resp)

    result = _fetch_french_title("12345", "fake-key")
    assert result == "Le Syndicat du Crime"


def test_fetch_french_title_returns_none_on_http_error(mocker):
    mock_resp = mocker.MagicMock()
    mock_resp.status_code = 404
    mocker.patch("letterboxd_data_management.get_letterboxd_data.requests.get", return_value=mock_resp)

    result = _fetch_french_title("12345", "fake-key")
    assert result is None


def test_fetch_french_title_returns_none_when_tmdb_id_falsy(mocker):
    mock_get = mocker.patch("letterboxd_data_management.get_letterboxd_data.requests.get")

    assert _fetch_french_title(None, "fake-key") is None
    assert _fetch_french_title("", "fake-key") is None
    mock_get.assert_not_called()


def test_fetch_french_title_returns_none_when_api_key_empty(mocker):
    mock_get = mocker.patch("letterboxd_data_management.get_letterboxd_data.requests.get")

    assert _fetch_french_title("12345", "") is None
    mock_get.assert_not_called()


def test_fetch_movie_includes_french_title(mocker, make_movie):
    movie_mock = make_movie()
    movie_mock.tmdb_id = "42"
    mocker.patch("letterboxd_data_management.get_letterboxd_data.Movie", return_value=movie_mock)
    mocker.patch(
        "letterboxd_data_management.get_letterboxd_data._fetch_french_title",
        return_value="Titre Français",
    )

    result = _fetch_movie("some-slug", api_key="fake-key")
    assert result is not None
    assert result["french_title"] == "Titre Français"
