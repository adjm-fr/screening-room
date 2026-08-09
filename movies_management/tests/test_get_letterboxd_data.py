"""
Tests for modules/get_letterboxd_data.py.

All tests are offline — no real API calls are made.
_fetch_movie and parquet I/O are mocked where needed.
"""

import logging
from datetime import date

import httpx
import pandas as pd
import pytest
import respx
from common.logging import RedactingFormatter
from modules.get_letterboxd_data import (
    TMDB_API_URL,
    Credits,
    _fetch_all,
    _fetch_credits,
    _fetch_french_title,
    _fetch_movie,
    _fetch_trailer,
    _get_tmdb_credits,
    _get_tmdb_movie,
    _get_tmdb_videos,
    get_letterboxd_data,
    refresh_letterboxd_data,
)
from tenacity import wait_none

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
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(genres=genres))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["genres"] == "Drama, Thriller"
    assert result["themes"] == "Revenge"
    assert result["mini_themes"] == "Heist"


def test_empty_genres_returns_none(mocker, make_movie):
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie())
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
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(details=details))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["studio"] == "A24"
    assert result["country"] == "USA, UK"


def test_crew_columns_left_for_tmdb_and_letterboxd_crew_ignored(mocker, make_movie):
    """The crew columns are TMDB's job now — _fetch_movie must not read letterboxdpy's crew.

    Regression guard: a MagicMock returns a truthy auto-attribute for ``movie.crew``, so
    if the old Letterboxd extraction came back it would silently repopulate these columns
    instead of leaving them for ``_fetch_credits``.
    """
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie())
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["directors"] is None
    assert result["producers"] is None
    assert result["writers"] is None
    assert result["composers"] is None


def test_exception_returns_none(mocker):
    mocker.patch("modules.get_letterboxd_data.Movie", side_effect=Exception("network error"))
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
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-b", "title": "Movie B", "release_year": 2020},
    )
    result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

    assert set(result["slug"]) == {"slug-a", "slug-b"}


def test_integration_date_set_to_today_for_new_slugs(tmp_path, mocker):
    cache_path = str(tmp_path / "cache.parquet")
    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "Movie A", "release_year": 2020},
    )
    result = get_letterboxd_data(["slug-a"], cache_path)

    today = pd.to_datetime(date.today())
    assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today


def test_failed_fetch_is_skipped_gracefully(tmp_path, mocker):
    cache_path = str(tmp_path / "cache.parquet")
    mocker.patch("modules.get_letterboxd_data._fetch_movie", return_value=None)
    result = get_letterboxd_data(["bad-slug"], cache_path)

    assert result.empty


def test_no_cache_file_starts_fresh(tmp_path, mocker):
    cache_path = str(tmp_path / "nonexistent.parquet")
    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
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


def test_empty_refresh_list_returns_df_unchanged(refresh_df):
    result = refresh_letterboxd_data(refresh_df, [], "")
    pd.testing.assert_frame_equal(result, refresh_df)


def test_refreshed_slug_gets_updated_fields(refresh_df, mocker):
    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title"},
    )
    result = refresh_letterboxd_data(refresh_df, ["slug-a"], "")

    assert result.loc[result["slug"] == "slug-a", "title"].iloc[0] == "New Title"


def test_non_refreshed_slug_is_preserved(refresh_df, mocker):
    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title"},
    )
    result = refresh_letterboxd_data(refresh_df, ["slug-a"], "")

    assert result.loc[result["slug"] == "slug-b", "title"].iloc[0] == "Untouched"


def test_integration_date_updated_on_refresh(mocker):
    df = pd.DataFrame([{"slug": "slug-a", "title": "Movie A"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))

    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "Movie A"},
    )
    result = refresh_letterboxd_data(df, ["slug-a"], "")

    today = pd.to_datetime(date.today())
    assert result.loc[result["slug"] == "slug-a", "integration_date"].iloc[0] == today


def test_dead_slug_is_pruned_from_cache(mocker):
    df = pd.DataFrame([{"slug": "slug-a", "title": "Old Title"}, {"slug": "slug-b", "title": "Kept"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))

    mocker.patch("modules.get_letterboxd_data._fetch_movie", return_value=None)
    result = refresh_letterboxd_data(df, ["slug-a"], "")

    assert "slug-a" not in result["slug"].values
    assert "slug-b" in result["slug"].values


def test_refresh_adds_columns_missing_from_target_cache(mocker):
    """Regression test: DataFrame.update() silently ignores columns absent from the
    target, so a cache predating cast/trailer_url must still gain them on refresh
    instead of the refreshed values being dropped.
    """
    df = pd.DataFrame([{"slug": "slug-a", "title": "Old Title"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))
    assert "cast" not in df.columns
    assert "trailer_url" not in df.columns

    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title", "tmdb_id": "42"},
    )
    mocker.patch("modules.get_letterboxd_data._fetch_french_title", return_value=None)
    mocker.patch(
        "modules.get_letterboxd_data._fetch_credits",
        return_value=Credits(cast="Actor A, Actor B", directors="Jane Doe"),
    )
    mocker.patch("modules.get_letterboxd_data._fetch_trailer", return_value="https://www.youtube.com/watch?v=abc123")

    result = refresh_letterboxd_data(df, ["slug-a"], "fake-key")

    assert "cast" in result.columns
    assert "trailer_url" in result.columns
    assert "directors" in result.columns
    row = result.loc[result["slug"] == "slug-a"].iloc[0]
    assert row["cast"] == "Actor A, Actor B"
    assert row["trailer_url"] == "https://www.youtube.com/watch?v=abc123"
    assert row["directors"] == "Jane Doe"


# ── retry behaviour ───────────────────────────────────────────────────────────


def test_fetch_movie_retries_then_succeeds(mocker, make_movie):
    # First Letterboxd scrape blips, the retry succeeds — _build_movie retries transparently.
    mocker.patch(
        "modules.get_letterboxd_data.Movie",
        side_effect=[Exception("transient blip"), make_movie()],
    )
    result = _fetch_movie("some-slug")
    assert result is not None


# ── _fetch_french_title (async, httpx + respx) ────────────────────────────────


@respx.mock
async def test_fetch_french_title_returns_title_on_success():
    respx.get(f"{TMDB_API_URL}/movie/12345").mock(return_value=httpx.Response(200, json={"title": "Le Syndicat du Crime"}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_french_title(client, "12345", "fake-key")
    assert result == "Le Syndicat du Crime"


@respx.mock
async def test_fetch_french_title_returns_none_on_http_error():
    respx.get(f"{TMDB_API_URL}/movie/12345").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        result = await _fetch_french_title(client, "12345", "fake-key")
    assert result is None


@respx.mock
async def test_fetch_french_title_retries_on_transient_error():
    route = respx.get(f"{TMDB_API_URL}/movie/12345").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"title": "Titre"})]
    )
    async with httpx.AsyncClient() as client:
        result = await _fetch_french_title(client, "12345", "fake-key")
    assert result == "Titre"
    assert route.call_count == 2


async def test_fetch_french_title_returns_none_when_tmdb_id_falsy():
    async with httpx.AsyncClient() as client:
        assert await _fetch_french_title(client, None, "fake-key") is None
        assert await _fetch_french_title(client, "", "fake-key") is None


async def test_fetch_french_title_returns_none_when_api_key_empty():
    async with httpx.AsyncClient() as client:
        assert await _fetch_french_title(client, "12345", "") is None


@respx.mock
async def test_fetch_french_title_tolerates_unknown_fields():
    """extra="ignore" — TMDB sends far more fields than we read; none should reject the payload."""
    respx.get(f"{TMDB_API_URL}/movie/12345").mock(
        return_value=httpx.Response(
            200, json={"title": "Le Syndicat du Crime", "budget": 5_000_000, "genres": [{"id": 1, "name": "Drame"}]}
        )
    )
    async with httpx.AsyncClient() as client:
        result = await _fetch_french_title(client, "12345", "fake-key")
    assert result == "Le Syndicat du Crime"


@respx.mock
async def test_fetch_french_title_logs_warning_on_shape_change(caplog):
    """A payload missing the required ``title`` field is a schema-drift bug, not a
    legitimate "no French title" case — it must log at WARNING (not the silent debug
    level every other failure uses) and still return the safe None fallback.
    """
    respx.get(f"{TMDB_API_URL}/movie/12345").mock(return_value=httpx.Response(200, json={"budget": 5_000_000}))
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_french_title(client, "12345", "fake-key")
    assert result is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


# ── _fetch_credits (async, httpx + respx) ───────────────────────────────────────


def _crew(name: str, job: str) -> dict:
    return {"name": name, "job": job, "department": "Directing"}


@respx.mock
async def test_fetch_credits_truncates_cast_to_top_8_comma_joined():
    cast = [{"name": f"Actor {i}", "order": i} for i in range(12)]
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"cast": cast}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.cast == ", ".join(f"Actor {i}" for i in range(8))


@respx.mock
async def test_fetch_credits_joins_all_cast_when_fewer_than_8():
    cast = [{"name": "Actor A", "order": 0}, {"name": "Actor B", "order": 1}]
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"cast": cast}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.cast == "Actor A, Actor B"


@respx.mock
async def test_fetch_credits_splits_crew_by_job():
    crew = [
        _crew("Jane Doe", "Director"),
        _crew("John Smith", "Producer"),
        _crew("Alice", "Producer"),
        _crew("Wanda", "Screenplay"),
        _crew("Nino R.", "Original Music Composer"),
        _crew("Bob", "Editor"),  # excluded — not a tracked job
        _crew("Eve", "Executive Producer"),  # excluded — narrower than TMDB's producer set
        _crew("Mary", "Novel"),  # excluded — Letterboxd keeps source-material credits separate
        _crew("DJ Source", "Music"),  # excluded — TMDB's loose job, credits source music too
    ]
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"crew": crew}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.directors == "Jane Doe"
    assert result.producers == "John Smith, Alice"
    assert result.writers == "Wanda"
    assert result.composers == "Nino R."


@respx.mock
async def test_fetch_credits_joins_multiple_composers():
    """Co-composed scores are real (Reznor/Ross, Carpenter/Lang) — ~6% of films."""
    crew = [_crew("Trent Reznor", "Original Music Composer"), _crew("Atticus Ross", "Original Music Composer")]
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"crew": crew}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.composers == "Trent Reznor, Atticus Ross"


@respx.mock
async def test_fetch_credits_dedupes_person_credited_under_two_jobs():
    """TMDB lists a person once per job, so a Writer+Screenplay credit must not double up."""
    crew = [_crew("Ann Writer", "Writer"), _crew("Ann Writer", "Screenplay"), _crew("Zed", "Writer")]
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"crew": crew}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.writers == "Ann Writer, Zed"


@respx.mock
async def test_fetch_credits_returns_none_fields_for_absent_roles():
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(
        return_value=httpx.Response(200, json={"cast": [], "crew": [_crew("Jane Doe", "Director")]})
    )
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.directors == "Jane Doe"
    assert result.cast is None
    assert result.producers is None
    assert result.writers is None
    assert result.composers is None


async def test_fetch_credits_returns_empty_when_tmdb_id_falsy():
    async with httpx.AsyncClient() as client:
        assert await _fetch_credits(client, None, "fake-key") == Credits()
        assert await _fetch_credits(client, "", "fake-key") == Credits()


async def test_fetch_credits_returns_empty_when_api_key_empty():
    async with httpx.AsyncClient() as client:
        assert await _fetch_credits(client, "12345", "") == Credits()


@respx.mock
async def test_fetch_credits_returns_empty_on_http_error():
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result == Credits()


@respx.mock
async def test_fetch_credits_tolerates_unknown_fields():
    """extra="ignore" — a real cast/crew entry carries ~20 fields we never read."""
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 12345,
                "cast": [
                    {"name": "Actor A", "order": 0, "gender": 2, "popularity": 12.3, "profile_path": "/x.jpg"},
                ],
                "crew": [
                    {"name": "Jane Doe", "job": "Director", "department": "Directing", "credit_id": "abc123"},
                ],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        result = await _fetch_credits(client, "12345", "fake-key")
    assert result.cast == "Actor A"
    assert result.directors == "Jane Doe"


@respx.mock
async def test_fetch_credits_logs_warning_when_cast_member_missing_name(caplog):
    """A cast entry with no ``name`` at all is a shape change, not a normal cast gap
    (real TMDB entries always carry a name) — must log at WARNING and still return the
    safe empty ``Credits()`` fallback.
    """
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(return_value=httpx.Response(200, json={"cast": [{"order": 0}]}))
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_credits(client, "12345", "fake-key")
    assert result == Credits()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


@respx.mock
async def test_fetch_credits_logs_warning_when_crew_is_wrong_type(caplog):
    """``directors`` is the taste ranker's highest-weighted dimension and the join's
    director confirmation — a ``crew`` field that stops being a list (e.g. TMDB starts
    sending an error string in its place) must be loud, not silently null every film.
    """
    respx.get(f"{TMDB_API_URL}/movie/12345/credits").mock(
        return_value=httpx.Response(200, json={"cast": [], "crew": "unexpected string, not a list"})
    )
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_credits(client, "12345", "fake-key")
    assert result == Credits()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


# ── _fetch_trailer (async, httpx + respx) ───────────────────────────────────────


def _video(key: str, lang: str | None, *, official: bool = True, site: str = "YouTube", video_type: str = "Trailer") -> dict:
    return {"key": key, "iso_639_1": lang, "official": official, "site": site, "type": video_type}


@respx.mock
async def test_fetch_trailer_prefers_french_over_english():
    videos = [_video("en-key", "en"), _video("fr-key", "fr")]
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": videos}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result == "https://www.youtube.com/watch?v=fr-key"


@respx.mock
async def test_fetch_trailer_falls_back_to_english_when_no_french():
    videos = [_video("de-key", "de"), _video("en-key", "en")]
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": videos}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result == "https://www.youtube.com/watch?v=en-key"


@respx.mock
async def test_fetch_trailer_falls_back_to_other_language_when_no_fr_or_en():
    videos = [_video("de-key", "de")]
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": videos}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result == "https://www.youtube.com/watch?v=de-key"


@respx.mock
async def test_fetch_trailer_excludes_unofficial_teaser_and_non_youtube():
    videos = [
        _video("unofficial-key", "fr", official=False),
        _video("teaser-key", "fr", video_type="Teaser"),
        _video("vimeo-key", "fr", site="Vimeo"),
    ]
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": videos}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result is None


@respx.mock
async def test_fetch_trailer_returns_none_on_empty_results():
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": []}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result is None


async def test_fetch_trailer_returns_none_when_tmdb_id_falsy():
    async with httpx.AsyncClient() as client:
        assert await _fetch_trailer(client, None, "fake-key") is None


async def test_fetch_trailer_returns_none_when_api_key_empty():
    async with httpx.AsyncClient() as client:
        assert await _fetch_trailer(client, "12345", "") is None


@respx.mock
async def test_fetch_trailer_returns_none_on_http_error():
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result is None


@respx.mock
async def test_fetch_trailer_tolerates_unknown_fields():
    """extra="ignore" — a real video entry carries fields (id, name, size, published_at, …) we never read."""
    videos = [
        {**_video("fr-key", "fr"), "id": "abc", "name": "Bande-annonce officielle", "size": 1080, "published_at": "2024-01-01"}
    ]
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": videos}))
    async with httpx.AsyncClient() as client:
        result = await _fetch_trailer(client, "12345", "fake-key")
    assert result == "https://www.youtube.com/watch?v=fr-key"


@respx.mock
async def test_fetch_trailer_logs_warning_on_shape_change(caplog):
    """``results`` no longer being a list (e.g. TMDB starts sending a dict/error blob in
    its place) must log at WARNING and still return the safe None fallback.
    """
    respx.get(f"{TMDB_API_URL}/movie/12345/videos").mock(return_value=httpx.Response(200, json={"results": "oops"}))
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_trailer(client, "12345", "fake-key")
    assert result is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


# ── _fetch_all TMDB enrichment integration ──────────────────────────────────────


@respx.mock
async def test_fetch_all_attaches_tmdb_enrichment(mocker, make_movie):
    movie_mock = make_movie()
    movie_mock.tmdb_id = "42"
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=movie_mock)
    respx.get(f"{TMDB_API_URL}/movie/42").mock(return_value=httpx.Response(200, json={"title": "Titre Français"}))
    respx.get(f"{TMDB_API_URL}/movie/42/credits").mock(
        return_value=httpx.Response(
            200,
            json={
                "cast": [{"name": "Actor A", "order": 0}],
                "crew": [
                    _crew("Jane Doe", "Director"),
                    _crew("John Smith", "Producer"),
                    _crew("Wanda", "Screenplay"),
                    _crew("Nino R.", "Original Music Composer"),
                ],
            },
        )
    )
    respx.get(f"{TMDB_API_URL}/movie/42/videos").mock(
        return_value=httpx.Response(200, json={"results": [_video("fr-key", "fr")]})
    )

    results = await _fetch_all(["some-slug"], api_key="fake-key")
    assert results[0] is not None
    assert results[0]["french_title"] == "Titre Français"
    assert results[0]["cast"] == "Actor A"
    assert results[0]["trailer_url"] == "https://www.youtube.com/watch?v=fr-key"
    # The crew columns now come from the same /credits round-trip as the cast.
    assert results[0]["directors"] == "Jane Doe"
    assert results[0]["producers"] == "John Smith"
    assert results[0]["writers"] == "Wanda"
    assert results[0]["composers"] == "Nino R."


@respx.mock
async def test_fetch_all_leaves_crew_null_without_api_key(mocker, make_movie):
    """Without a TMDB key the crew columns are null — `directors` included."""
    movie_mock = make_movie()
    movie_mock.tmdb_id = "42"
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=movie_mock)

    results = await _fetch_all(["some-slug"], api_key="")
    assert results[0] is not None
    assert results[0]["directors"] is None
    assert results[0]["producers"] is None
    assert results[0]["writers"] is None
    assert results[0]["composers"] is None
    assert results[0]["cast"] is None


@pytest.mark.asyncio
@respx.mock
async def test_tmdb_failures_never_log_the_api_key(caplog):
    """The key must not survive into a rendered log line, on the real fetch path.

    Uses a 500 because that is the branch that actually raises: ``_get_tmdb_*`` only
    calls ``raise_for_status`` for 429/5xx, and it is ``HTTPStatusError``'s string
    form that carries the full request URL (key included) into the log record.

    The record itself is *expected* to hold the key — the guarantee lives at the
    output boundary, in the formatter ``common.configure_logging`` installs, so it
    covers tracebacks and third-party loggers too. This renders the real records
    through that formatter, which is what the entry point wires up for production.
    """
    respx.get(url__startswith="https://api.themoviedb.org/3/movie/42").mock(return_value=httpx.Response(500))
    caplog.set_level(logging.DEBUG, logger="modules.get_letterboxd_data")
    # Retries are exponentially backed off; drop the wait so the test stays fast.
    for fn in (_get_tmdb_movie, _get_tmdb_credits, _get_tmdb_videos):
        fn.retry.wait = wait_none()

    async with httpx.AsyncClient() as client:
        assert await _fetch_french_title(client, "42", "SUPERSECRETKEY") is None
        assert await _fetch_credits(client, "42", "SUPERSECRETKEY") == Credits()
        assert await _fetch_trailer(client, "42", "SUPERSECRETKEY") is None

    assert caplog.records, "expected the failures to be logged at DEBUG"
    assert "api_key" in caplog.text, "expected the request URL in the log, else this test proves nothing"

    formatter = RedactingFormatter("%(message)s", secrets=["SUPERSECRETKEY"])
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert "SUPERSECRETKEY" not in rendered
    assert "***" in rendered
