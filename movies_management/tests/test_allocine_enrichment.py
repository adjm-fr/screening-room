"""Tests for modules/allocine_enrichment.py."""

import asyncio
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from common.parquet_io import SchemaValidationError
from contracts import DATA_LETTERBOXD
from modules.allocine_enrichment import (
    _build_cache_index,
    _director_tokens,
    _directors_overlap,
    _match_cache,
    _normalize_title,
    _parse_runtime,
    _search_letterboxd_slug,
    _split_director_tokens,
    enrich_cache_from_showtimes,
    resolve_slug_from_allocine_tuple,
)

# ── director token matching ───────────────────────────────────────────────────


def test_director_tokens_strips_accents_and_punctuation():
    assert _director_tokens("S.S. Rajamouli") == frozenset({"s", "rajamouli"})
    assert _director_tokens("S. S. Rajamouli") == frozenset({"s", "rajamouli"})
    assert _director_tokens("Víctor Erice") == _director_tokens("Victor Erice")


def test_directors_overlap_is_containment_not_equality():
    # Allocine's dotted-initial spacing differs from Letterboxd's, but tokens match
    assert _directors_overlap(_split_director_tokens("S.S. Rajamouli", "|"), _split_director_tokens("S. S. Rajamouli", "|"))
    # A suffix like "Jr." on one side still overlaps
    assert _directors_overlap(_split_director_tokens("Akinola Davies Jr.", "|"), _split_director_tokens("Akinola Davies", "|"))
    # Genuinely different directors never overlap
    assert not _directors_overlap(_split_director_tokens("Ridley Scott", "|"), _split_director_tokens("Someone Else", "|"))


def test_normalize_title_strips_accents_and_case():
    assert _normalize_title("RRR") == "rrr"
    assert _normalize_title("L'Esprit de la ruche") == _normalize_title("l esprit de la ruche")
    assert _normalize_title(None) == ""
    assert _normalize_title(float("nan")) == ""


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


def test_search_matches_director_with_different_initial_spacing(mocker):
    # Regression: Allocine sends "S.S. Rajamouli", Letterboxd carries "S. S. Rajamouli" —
    # exact string/set equality used to reject this and drop RRR as unresolved.
    results = [{"slug": "rrr", "year": 2022, "directors": [{"name": "S. S. Rajamouli"}]}]
    mocker.patch(
        "modules.allocine_enrichment.Search",
        return_value=mocker.MagicMock(results={"results": results}),
    )
    assert _search_letterboxd_slug("RRR", "2022", "S.S. Rajamouli") == "rrr"


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


# ── _build_cache_index / _match_cache ────────────────────────────────────────


def test_match_cache_recovers_director_spelling_drift():
    cache_df = pd.DataFrame(
        [
            {
                "slug": "rrr",
                "title": "RRR",
                "french_title": "Rise Roar Revolt (RRR)",
                "directors": "S. S. Rajamouli",
                "release_year": 2022,
            }
        ]
    )
    index = _build_cache_index(cache_df)
    film = {"title": "RRR", "original_title": "RRR", "director": "S.S. Rajamouli", "release_year": 2022}
    assert _match_cache(film, index) == "rrr"


def test_match_cache_rejects_year_mismatch_on_recurring_title():
    # "Le Retour" — two unrelated films sharing a French title, different years/directors
    cache_df = pd.DataFrame(
        [
            {"slug": "the-return", "french_title": "Le Retour", "directors": "Andrey Zvyagintsev", "release_year": 2003},
            {"slug": "homecoming-2023", "french_title": "Le retour", "directors": "Catherine Corsini", "release_year": 2023},
        ]
    )
    index = _build_cache_index(cache_df)
    film = {"title": "Le Retour", "original_title": None, "director": "Andrey Zvyagintsev", "release_year": 2023}
    # Year (2023) matches the Corsini row, but its director doesn't overlap — no match
    assert _match_cache(film, index) is None


def test_match_cache_requires_director_on_both_sides():
    cache_df = pd.DataFrame([{"slug": "some-film", "title": "Some Film", "directors": None, "release_year": 2020}])
    index = _build_cache_index(cache_df)
    film = {"title": "Some Film", "original_title": None, "director": "A Director", "release_year": 2020}
    assert _match_cache(film, index) is None


def test_build_cache_index_tolerates_nan_directors():
    # Regression: a real cache row can carry a NaN `directors` cell (float, not str/None).
    # bool(float("nan")) is True, so a plain `if not value` guard lets it reach .split()
    # and crash — this must not raise.
    cache_df = pd.DataFrame([{"slug": "some-film", "title": "Some Film", "directors": float("nan"), "release_year": 2020}])
    index = _build_cache_index(cache_df)
    film = {"title": "Some Film", "original_title": None, "director": "A Director", "release_year": 2020}
    assert _match_cache(film, index) is None


def test_match_cache_returns_none_without_parseable_year():
    index = _build_cache_index(pd.DataFrame([{"slug": "x", "title": "X", "directors": "A B", "release_year": 2020}]))
    film = {"title": "X", "original_title": None, "director": "A B", "release_year": None}
    assert _match_cache(film, index) is None


# ── _parse_runtime ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2h 48min", 168.0),  # the only shape the real Allocine feed emits
        ("0h 33min", 33.0),  # a short: hours present but zero
        ("1h", 60.0),
        ("95min", 95.0),
        ("2h12", None),  # unanchored parsing would read this as 120 — see _RUNTIME_RE
        ("about two hours", None),
        ("", None),
        (None, None),  # SHOWTIMES allows a null runtime
        (float("nan"), None),
        (168, None),  # the contract says raw string, not parsed minutes
    ],
)
def test_parse_runtime(raw, expected):
    assert _parse_runtime(raw) == expected


# ── _match_cache tier 2: runtime proximity ────────────────────────────────────


def _bergman_cache():
    """The real *Scenes from a Marriage* collision: the 1974 film vs the 1973 TV cut."""
    return pd.DataFrame(
        [
            {
                "slug": "scenes-from-a-marriage",
                "title": "Scenes from a Marriage",
                "french_title": "Scènes de la vie conjugale",
                "original_title": "Scener ur ett äktenskap",
                "directors": "Ingmar Bergman",
                "release_year": 1974,
                "runtime": 169.0,
            }
        ]
    )


def _bergman_film(**overrides):
    film = {
        "title": "Scènes de la vie conjugale",
        "original_title": "Scener ur ett äktenskap",
        "director": "Ingmar Bergman",
        "release_year": 1973,  # Allocine carries the production year
        "runtime": "2h 48min",  # 168 min, 1 off the cache's 169
    }
    return film | overrides


def test_match_cache_falls_back_to_runtime_when_the_year_disagrees():
    # Allocine says 1973, the cache row says 1974 — without the fallback this film
    # misses the cache and the live search resolves the 281-min TV entry instead.
    assert _match_cache(_bergman_film(), _build_cache_index(_bergman_cache())) == "scenes-from-a-marriage"


def test_match_cache_tier1_wins_when_the_year_matches():
    # Two candidates: the exact-year one has a far-off runtime, the other is within
    # tolerance. The year must decide — runtime is never consulted while it can.
    cache_df = pd.DataFrame(
        [
            {"slug": "right-year", "title": "Twin", "directors": "A Director", "release_year": 1980, "runtime": 200.0},
            {"slug": "close-runtime", "title": "Twin", "directors": "A Director", "release_year": 1999, "runtime": 100.0},
        ]
    )
    film = {"title": "Twin", "original_title": None, "director": "A Director", "release_year": 1980, "runtime": "1h 40min"}
    assert _match_cache(film, _build_cache_index(cache_df)) == "right-year"


def test_match_cache_refuses_when_two_candidates_are_within_tolerance():
    # The real `Paranoia` collision: two different Umberto Lenzi films released in
    # France under the same title, 3 minutes apart. Only the year ever separated
    # them, so when the year matches neither, refusing beats guessing.
    cache_df = pd.DataFrame(
        [
            {
                "slug": "paranoia-1969",
                "french_title": "Paranoia",
                "directors": "Umberto Lenzi",
                "release_year": 1969,
                "runtime": 91.0,
            },
            {
                "slug": "a-quiet-place-to-kill",
                "french_title": "Paranoia",
                "directors": "Umberto Lenzi",
                "release_year": 1970,
                "runtime": 94.0,
            },
        ]
    )
    film = {"title": "Paranoia", "original_title": None, "director": "Umberto Lenzi", "release_year": 1968, "runtime": "1h 31min"}
    assert _match_cache(film, _build_cache_index(cache_df)) is None


def test_match_cache_tier2_ignores_candidates_outside_tolerance():
    # 169 vs 120 min is not the same film, whatever the title and director say.
    assert _match_cache(_bergman_film(runtime="2h 00min"), _build_cache_index(_bergman_cache())) is None


@pytest.mark.parametrize("runtime", [None, float("nan"), "", "unknown", 168])
def test_match_cache_tier2_skipped_without_a_parseable_allocine_runtime(runtime):
    # A null runtime is contract-legal — it must skip the tier, never raise.
    assert _match_cache(_bergman_film(runtime=runtime), _build_cache_index(_bergman_cache())) is None


def test_match_cache_tier2_skipped_when_the_cache_row_has_no_runtime():
    cache_df = _bergman_cache()
    cache_df["runtime"] = float("nan")
    assert _match_cache(_bergman_film(), _build_cache_index(cache_df)) is None


def test_match_cache_tier2_missing_runtime_key_does_not_raise():
    # Callers that build a film dict by hand (as several tests here do) simply get
    # tier 1 — the fallback is opt-in on the key's presence, not a required field.
    film = _bergman_film()
    del film["runtime"]
    assert _match_cache(film, _build_cache_index(_bergman_cache())) is None


def test_match_cache_tier2_counts_one_cache_row_once():
    # _build_cache_index files a row under every title spelling it carries, so this
    # row is reachable via both `title` and `original_title`. Counting entries
    # rather than slugs would call it ambiguous and refuse a legitimate match.
    cache_df = pd.DataFrame(
        [
            {
                "slug": "solaris",
                "title": "Solaris",
                "french_title": "Solaris",
                "original_title": "Solaris",
                "directors": "Andrei Tarkovsky",
                "release_year": 1972,
                "runtime": 167.0,
            }
        ]
    )
    film = {
        "title": "Solaris",
        "original_title": "Solaris",
        "director": "Andrei Tarkovsky",
        "release_year": 1971,
        "runtime": "2h 47min",
    }
    assert _match_cache(film, _build_cache_index(cache_df)) == "solaris"


def test_build_cache_index_tolerates_a_non_numeric_cache_runtime():
    # The cache's `runtime` is float64, but index building must not crash the whole
    # enrichment run on a malformed cell — the same tolerance the adjacent
    # `release_year` parse has always had. The row just loses its tier-2 eligibility.
    cache_df = _bergman_cache()
    cache_df["runtime"] = "not a number"
    assert _match_cache(_bergman_film(), _build_cache_index(cache_df)) is None


def test_match_cache_tier2_still_requires_director_overlap():
    assert _match_cache(_bergman_film(director="Someone Else"), _build_cache_index(_bergman_cache())) is None


# ── enrich_cache_from_showtimes ───────────────────────────────────────────────


def test_enrich_resolves_from_cache_without_a_live_search(mocker, tmp_path):
    # RRR is already in the cache under a different director spacing — enrich should
    # find it via _match_cache and never touch the network or unresolved_allocine.
    showtimes = pd.DataFrame(
        [
            {
                "movie": "RRR",
                "original_title": "RRR",
                "director": "S.S. Rajamouli",
                "release_year": 2022,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "3h 07min",
                "showtimes": ["2026-08-10T20:00"],
            }
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    cache_df = pd.DataFrame([{"slug": "rrr", "title": "RRR", "directors": "S. S. Rajamouli", "release_year": 2022}])
    cache_path = tmp_path / "cache.parquet"
    cache_df.to_parquet(cache_path)

    resolve_mock = mocker.patch("modules.allocine_enrichment.resolve_slug_from_allocine_tuple", new_callable=AsyncMock)
    get_data_mock = mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    unresolved_path = tmp_path / "unresolved.parquet"
    enrich_cache_from_showtimes(showtimes_path, cache_path, unresolved_path)

    resolve_mock.assert_not_called()
    get_data_mock.assert_not_called()
    assert pd.read_parquet(unresolved_path).empty


def test_enrich_resolves_from_cache_via_the_runtime_fallback(mocker, tmp_path):
    # End-to-end of the tier-2 rescue: Allocine's production year (1973) disagrees
    # with the cache's release year (1974), so only the runtime can match it. Before
    # the fallback this reached the live search and resolved the wrong entry.
    showtimes = pd.DataFrame(
        [
            {
                "movie": "Scènes de la vie conjugale",
                "original_title": "Scener ur ett äktenskap",
                "director": "Ingmar Bergman",
                "release_year": 1973,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "2h 48min",
                "showtimes": ["2026-08-10T20:00"],
            }
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    cache_path = tmp_path / "cache.parquet"
    _bergman_cache().to_parquet(cache_path)

    resolve_mock = mocker.patch("modules.allocine_enrichment.resolve_slug_from_allocine_tuple", new_callable=AsyncMock)
    get_data_mock = mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    unresolved_path = tmp_path / "unresolved.parquet"
    enrich_cache_from_showtimes(showtimes_path, cache_path, unresolved_path)

    resolve_mock.assert_not_called()
    get_data_mock.assert_not_called()
    assert pd.read_parquet(unresolved_path).empty


def test_enrich_unresolved_parquet_keeps_the_four_tuple_key(mocker, tmp_path):
    # `runtime` rides along in the film dict for the fallback tier, but must not
    # leak into unresolved_allocine.parquet — cinema_dashboard's
    # build_unresolved_showtimes joins back on exactly these four columns.
    showtimes = pd.DataFrame(
        [
            {
                "movie": "Unknown Film",
                "original_title": None,
                "director": "A Director",
                "release_year": 2024,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "1h 40min",
                "showtimes": ["2026-08-10T20:00"],
            }
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    mocker.patch("modules.allocine_enrichment.resolve_slug_from_allocine_tuple", new_callable=AsyncMock, return_value=None)
    mocker.patch("modules.allocine_enrichment.get_letterboxd_data")

    unresolved_path = tmp_path / "unresolved.parquet"
    enrich_cache_from_showtimes(showtimes_path, tmp_path / "cache.parquet", unresolved_path)

    assert list(pd.read_parquet(unresolved_path).columns) == ["movie", "original_title", "director", "release_year"]


def test_enrich_resolves_new_slugs_and_calls_get_letterboxd_data(mocker, tmp_path):
    showtimes_row = {
        "movie": "Parasite",
        "original_title": "Gisaengchung",
        "director": "Bong Joon-ho",
        "release_year": 2019,
        "theater_id": "t1",
        "theater_name": "Cinema One",
        "runtime": "2h 12min",
        "showtimes": ["2026-08-10T20:00"],
    }
    showtimes = pd.DataFrame([showtimes_row, dict(showtimes_row)])  # second row is a duplicate
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

    get_data_mock.assert_called_once_with(["parasite-2019"], cache_path, "", tmdb_groups=frozenset())


def test_enrich_stamps_allocine_source_on_new_rows(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [
            {
                "movie": "Parasite",
                "original_title": "Gisaengchung",
                "director": "Bong Joon-ho",
                "release_year": 2019,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "2h 12min",
                "showtimes": ["2026-08-10T20:00"],
            }
        ]
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
    # Allocine pipeline stamps "allocine_showtimes" and writes. The returned frame must
    # carry every DATA_LETTERBOXD required column since the write is now validated.
    new_row = dict.fromkeys(DATA_LETTERBOXD.required_columns)
    new_row.update({"slug": "parasite-2019", "title": "Parasite"})
    mocker.patch(
        "modules.allocine_enrichment.get_letterboxd_data",
        return_value=pd.DataFrame([new_row]),
    )

    enrich_cache_from_showtimes(showtimes_path, cache_path, tmp_path / "unresolved.parquet")

    saved = pd.read_parquet(cache_path)
    assert saved.loc[saved["slug"] == "parasite-2019", "source"].iloc[0] == "allocine_showtimes"


def test_enrich_skips_already_cached_slugs(mocker, tmp_path):
    showtimes = pd.DataFrame(
        [
            {
                "movie": "Parasite",
                "original_title": None,
                "director": "Bong Joon-ho",
                "release_year": 2019,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "2h 12min",
                "showtimes": ["2026-08-10T20:00"],
            },
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
            {
                "movie": "Unknown Film",
                "original_title": None,
                "director": None,
                "release_year": 2024,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "1h 40min",
                "showtimes": ["2026-08-10T20:00"],
            },
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


# ── contract enforcement ──────────────────────────────────────────────────────


def test_enrich_rejects_showtimes_missing_a_required_column(tmp_path):
    # No "showtimes" column (a SHOWTIMES-required column) — the validated read
    # must reject this before any resolution logic runs.
    showtimes = pd.DataFrame(
        [
            {
                "movie": "RRR",
                "original_title": "RRR",
                "director": "S.S. Rajamouli",
                "release_year": 2022,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "3h 07min",
            }
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)

    with pytest.raises(SchemaValidationError, match="showtimes"):
        enrich_cache_from_showtimes(showtimes_path, tmp_path / "cache.parquet", tmp_path / "unresolved.parquet")


def test_enrich_rejects_a_cache_write_missing_a_required_column(mocker, tmp_path):
    # get_letterboxd_data returns a frame missing DATA_LETTERBOXD-required columns
    # (e.g. "title") — the validated cache write must reject it before persisting.
    showtimes = pd.DataFrame(
        [
            {
                "movie": "Parasite",
                "original_title": "Gisaengchung",
                "director": "Bong Joon-ho",
                "release_year": 2019,
                "theater_id": "t1",
                "theater_name": "Cinema One",
                "runtime": "2h 12min",
                "showtimes": ["2026-08-10T20:00"],
            }
        ]
    )
    showtimes_path = tmp_path / "showtimes.parquet"
    showtimes.to_parquet(showtimes_path)
    cache_path = tmp_path / "cache.parquet"

    mocker.patch(
        "modules.allocine_enrichment.resolve_slug_from_allocine_tuple",
        new_callable=mocker.AsyncMock,
        return_value="parasite-2019",
    )
    incomplete_row = dict.fromkeys(DATA_LETTERBOXD.required_columns)
    incomplete_row["slug"] = "parasite-2019"
    del incomplete_row["title"]  # drop a required column
    mocker.patch(
        "modules.allocine_enrichment.get_letterboxd_data",
        return_value=pd.DataFrame([incomplete_row]),
    )

    with pytest.raises(SchemaValidationError, match="title"):
        enrich_cache_from_showtimes(showtimes_path, cache_path, tmp_path / "unresolved.parquet")
