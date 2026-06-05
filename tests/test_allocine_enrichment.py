"""Tests for modules/allocine_enrichment.py."""

import asyncio
from unittest.mock import AsyncMock

import pandas as pd

from modules.allocine_enrichment import (
    _search_letterboxd_slug,
    enrich_cache_from_showtimes,
    resolve_slug_from_allocine_tuple,
)

# ── _search_letterboxd_slug ───────────────────────────────────────────────────


def test_search_returns_slug_on_year_and_director_match(mocker):
    results = [
        {"slug": "the-godfather", "year": 1972, "directors": [{"name": "Francis Ford Coppola"}]},
        {"slug": "other-film", "year": 1972, "directors": [{"name": "Someone Else"}]},
    ]
    mocker.patch(
        "modules.allocine_enrichment.Search",
        return_value=mocker.MagicMock(results={"results": results}),
    )
    assert _search_letterboxd_slug("The Godfather", "1972", "Francis Ford Coppola") == "the-godfather"


def test_search_returns_none_when_director_does_not_match(mocker):
    results = [
        {"slug": "blade-runner", "year": 1982, "directors": [{"name": "Ridley Scott"}]},
    ]
    mocker.patch(
        "modules.allocine_enrichment.Search",
        return_value=mocker.MagicMock(results={"results": results}),
    )
    # Year matches but director doesn't — no slug returned (no year-only fallback)
    assert _search_letterboxd_slug("Blade Runner", "1982", "Unknown Director") is None


def test_search_returns_none_on_exception(mocker):
    mocker.patch("modules.allocine_enrichment.Search", side_effect=Exception("network error"))
    assert _search_letterboxd_slug("Anything", "2020", None) is None


def test_search_retries_then_succeeds(mocker):
    # First Letterboxd search blips, the retry returns a matching result.
    results = [{"slug": "the-godfather", "year": 1972, "directors": [{"name": "Francis Ford Coppola"}]}]
    mocker.patch(
        "modules.allocine_enrichment.Search",
        side_effect=[Exception("transient blip"), mocker.MagicMock(results={"results": results})],
    )
    assert _search_letterboxd_slug("The Godfather", "1972", "Francis Ford Coppola") == "the-godfather"


# ── resolve_slug_from_allocine_tuple ─────────────────────────────────────────


def test_resolve_returns_slug_from_letterboxd_search(mocker):
    mocker.patch(
        "modules.allocine_enrichment._search_letterboxd_slug",
        return_value="parasite-2019",
    )
    slug = asyncio.run(resolve_slug_from_allocine_tuple("Parasite", None, "Bong Joon-ho", 2019))
    assert slug == "parasite-2019"


def test_resolve_falls_back_to_original_title(mocker):
    # French title yields nothing; original English title resolves it
    search = mocker.patch("modules.allocine_enrichment._search_letterboxd_slug", side_effect=[None, "dead-mans-wire"])
    slug = asyncio.run(resolve_slug_from_allocine_tuple("La corde au cou", "Dead Man's Wire", "John Doe", 1965))
    assert slug == "dead-mans-wire"
    assert search.call_count == 2


def test_resolve_returns_none_when_letterboxd_misses(mocker):
    mocker.patch("modules.allocine_enrichment._search_letterboxd_slug", return_value=None)
    assert asyncio.run(resolve_slug_from_allocine_tuple("Unknown Film", None, None, 2024)) is None


# ── enrich_cache_from_showtimes ───────────────────────────────────────────────


def test_enrich_resolves_new_slugs_and_calls_get_letterboxd_data(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [
            {"movie": "Parasite", "original_title": "Gisaengchung", "director": "Bong Joon-ho", "release_year": 2019},
            {
                "movie": "Parasite",
                "original_title": "Gisaengchung",
                "director": "Bong Joon-ho",
                "release_year": 2019,
            },  # duplicate
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    # Empty cache (no pre-existing slugs)
    cache_path = tmp_path / "cache.parquet"

    mocker.patch(
        "modules.allocine_enrichment.resolve_slug_from_allocine_tuple",
        new_callable=AsyncMock,
        return_value="parasite-2019",
    )
    get_data_mock = mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    enrich_cache_from_showtimes(showtimes_path, cache_path, tmp_path / "unresolved.parquet")

    get_data_mock.assert_called_once_with(["parasite-2019"], cache_path, "")


def test_enrich_stamps_allocine_source_on_new_rows(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [{"movie": "Parasite", "original_title": "Gisaengchung", "director": "Bong Joon-ho", "release_year": 2019}]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)
    cache_path = tmp_path / "cache.parquet"

    mocker.patch(
        "modules.allocine_enrichment.resolve_slug_from_allocine_tuple",
        new_callable=mocker.AsyncMock,
        return_value="parasite-2019",
    )
    # get_letterboxd_data no longer persists — it returns the combined cache; the
    # Allocine pipeline stamps "allocine_showtimes" and writes.
    mocker.patch(
        "modules.allocine_enrichment.get_letterboxd_data",
        return_value=pd.DataFrame([{"slug": "parasite-2019", "title": "Parasite"}]),
    )

    enrich_cache_from_showtimes(showtimes_path, cache_path, tmp_path / "unresolved.parquet")

    saved = pd.read_parquet(cache_path)
    assert saved.loc[saved["slug"] == "parasite-2019", "source"].iloc[0] == "allocine_showtimes"


def test_enrich_skips_already_cached_slugs(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [
            {"movie": "Parasite", "original_title": None, "director": "Bong Joon-ho", "release_year": 2019},
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    # Cache already contains this slug
    cache_df = pd.DataFrame([{"slug": "parasite-2019", "tmdb_id": "496243"}])
    cache_path = tmp_path / "cache.parquet"
    cache_df.to_parquet(cache_path)

    mocker.patch("modules.allocine_enrichment.resolve_slug_from_allocine_tuple", return_value="parasite-2019")
    get_data_mock = mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    enrich_cache_from_showtimes(showtimes_path, cache_path, tmp_path / "unresolved.parquet")

    get_data_mock.assert_not_called()


def test_enrich_writes_unresolved_parquet(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [
            {"movie": "Unknown Film", "original_title": None, "director": None, "release_year": 2024},
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    mocker.patch("modules.allocine_enrichment.resolve_slug_from_allocine_tuple", new_callable=AsyncMock, return_value=None)
    mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    unresolved_path = tmp_path / "unresolved.parquet"
    enrich_cache_from_showtimes(showtimes_path, tmp_path / "cache.parquet", unresolved_path)

    unresolved_df = pd.read_parquet(unresolved_path)
    assert len(unresolved_df) == 1
    assert unresolved_df.iloc[0]["movie"] == "Unknown Film"
