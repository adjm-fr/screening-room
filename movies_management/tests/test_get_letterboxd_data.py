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
    TmdbColumns,
    _fetch_all,
    _fetch_bundle,
    _fetch_french_title,
    _fetch_movie,
    _get_tmdb_bundle,
    _get_tmdb_movie,
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


def test_genres_is_withheld_for_tmdb_but_themes_split_by_type(mocker, make_movie):
    """`genres` is TMDB's now (see CACHE_COLUMNS.md), so _fetch_movie leaves it None even
    when Letterboxd's own genre block carries a value — only themes/mini_themes are its job.
    """
    genres = [
        {"type": "genre", "name": "Drama"},
        {"type": "genre", "name": "Thriller"},
        {"type": "theme", "name": "Revenge"},
        {"type": "mini-theme", "name": "Heist"},
    ]
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(genres=genres))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["genres"] is None
    assert result["themes"] == "Revenge"
    assert result["mini_themes"] == "Heist"


def test_empty_genres_returns_none(mocker, make_movie):
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie())
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["genres"] is None
    assert result["themes"] is None
    assert result["mini_themes"] is None


_TERRITORY_DETAILS = [
    {"type": "studio", "name": "A24"},
    {"type": "country", "name": "USA"},
    {"type": "country", "name": "UK"},
    {"type": "language", "name": "English"},
]


def test_tmdb_owned_detail_types_are_dropped_not_expanded(mocker, make_movie):
    """Letterboxd still serves studio/country/language on the page; TMDB owns those columns
    now, so all three must be filtered out of ``**details_by_type`` rather than expanded.

    The sharp regression this pins: that expansion is the *last* entry in the returned
    dict literal, so a surviving Letterboxd value would overwrite the TMDB one
    ``_fetch_all`` seeds above it — right values fetched, wrong values written, nothing
    raised. Asserting None (not "A24") is what catches that.
    """
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(details=_TERRITORY_DETAILS))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["studio"] is None
    assert result["country"] is None
    assert result["language"] is None
    assert result["origin_country"] is None
    assert result["original_language"] is None


def test_placeholder_territory_and_taxonomy_columns_always_present(mocker, make_movie):
    """Every TMDB-sourced column is a key of the returned dict even with nothing to fill it
    — the contract requires all seven, so a film with no Letterboxd detail types at all
    must still carry them as None rather than the column vanishing from the frame.
    """
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(details=[]))
    result = _fetch_movie("some-slug")
    assert result is not None
    for column in ("studio", "country", "origin_country", "language", "original_language", "genres", "keywords"):
        assert column in result
        assert result[column] is None


def test_unknown_detail_types_still_expand(mocker, make_movie):
    """The filter scopes to the three TMDB-owned types — any other type still expands."""
    details = [
        {"type": "studio", "name": "A24"},
        {"type": "format", "name": "IMAX"},
        {"type": "format", "name": "70mm"},
    ]
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=make_movie(details=details))
    result = _fetch_movie("some-slug")
    assert result is not None
    assert result["format"] == "IMAX, 70mm"


def test_crew_columns_left_for_tmdb_and_letterboxd_crew_ignored(mocker, make_movie):
    """The crew columns are TMDB's job now — _fetch_movie must not read letterboxdpy's crew.

    Regression guard: a MagicMock returns a truthy auto-attribute for ``movie.crew``, so
    if the old Letterboxd extraction came back it would silently repopulate these columns
    instead of leaving them for ``_fetch_bundle``.
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


def test_schema_migration_columns_seeded_with_nothing_to_fetch(tmp_path, cache_df):
    """The quiet run is the one that breaks: nothing new, nothing stale, and a full cache.

    Age is the only refresh trigger, so a cache just rewritten by a backfill has no stale
    rows, and no new slugs means no concat either — nothing else would ever add a column
    introduced after those rows were written. Without this seeding the run reaches
    `write_parquet_validated` with a pre-migration frame and fails the contract every time,
    with `--reset_database` as the only exit.
    """
    cache_path = str(tmp_path / "cache.parquet")
    cache_df.to_parquet(cache_path, index=False)
    assert "origin_country" not in cache_df.columns

    result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

    # Present, so the contract holds; null, because nothing was migrated.
    for column in ("origin_country", "original_language"):
        assert column in result.columns
        assert result[column].isna().all()


def test_schema_migration_leaves_a_populated_column_alone(tmp_path, cache_df):
    """Seeding must never clobber values a previous migrated run already wrote."""
    cache_path = str(tmp_path / "cache.parquet")
    migrated = cache_df.assign(origin_country=["US", "FR"], original_language=["en", "fr"])
    migrated.to_parquet(cache_path, index=False)

    result = get_letterboxd_data(["slug-a", "slug-b"], cache_path)

    assert result.sort_values("slug")["origin_country"].tolist() == ["US", "FR"]
    assert result.sort_values("slug")["original_language"].tolist() == ["en", "fr"]


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
        "modules.get_letterboxd_data._fetch_bundle",
        return_value=TmdbColumns(
            credits=Credits(cast="Actor A, Actor B", directors="Jane Doe"),
            trailer_url="https://www.youtube.com/watch?v=abc123",
            origin_country="US",
            original_language="en",
        ),
    )

    result = refresh_letterboxd_data(df, ["slug-a"], "fake-key")

    assert "cast" in result.columns
    assert "trailer_url" in result.columns
    assert "directors" in result.columns
    row = result.loc[result["slug"] == "slug-a"].iloc[0]
    assert row["cast"] == "Actor A, Actor B"
    assert row["trailer_url"] == "https://www.youtube.com/watch?v=abc123"
    # origin_country/original_language exist on no cached row anywhere, so this loop is the
    # only thing that lets the real 6,764-row cache satisfy the tightened contract without
    # a --reset_database. Losing it would leave both null forever, silently.
    assert row["origin_country"] == "US"
    assert row["original_language"] == "en"
    assert row["directors"] == "Jane Doe"


def test_refresh_leaves_a_null_bundle_field_null(mocker):
    """A TMDB field the bundle has nothing for stays null — `_fetch_all` writes every
    migrated column unconditionally now, so there is no Letterboxd fallback to preserve.
    """
    df = pd.DataFrame([{"slug": "slug-a", "title": "Old Title"}])
    df["integration_date"] = pd.to_datetime(date(2023, 1, 1))

    mocker.patch(
        "modules.get_letterboxd_data._fetch_movie",
        return_value={"slug": "slug-a", "title": "New Title", "tmdb_id": "42"},
    )
    mocker.patch("modules.get_letterboxd_data._fetch_french_title", return_value=None)
    mocker.patch("modules.get_letterboxd_data._fetch_bundle", return_value=TmdbColumns(studio="Gaumont"))

    result = refresh_letterboxd_data(df, ["slug-a"], "fake-key")

    row = result.loc[result["slug"] == "slug-a"].iloc[0]
    assert row["studio"] == "Gaumont"
    assert pd.isna(row["country"])
    assert pd.isna(row["origin_country"])
    assert pd.isna(row["language"])
    assert pd.isna(row["original_language"])
    assert pd.isna(row["genres"])
    assert pd.isna(row["keywords"])


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


# ── _fetch_bundle: credits half (async, httpx + respx) ──────────────────────────


def _crew(name: str, job: str) -> dict:
    return {"name": name, "job": job, "department": "Directing"}


def _video(key: str, lang: str | None, *, official: bool = True, site: str = "YouTube", video_type: str = "Trailer") -> dict:
    return {"key": key, "iso_639_1": lang, "official": official, "site": site, "type": video_type}


def _bundle_json(*, cast=None, crew=None, videos=None, credits=None, keywords=None, genres=None) -> dict:
    """One ``append_to_response=credits,videos,keywords`` payload.

    All three blocks are always present because ``MovieBundle`` requires them — TMDB
    omitting one is the schema drift the model exists to catch, which is its own test
    below. ``genres`` is a plain base-payload field, so it is optional and only appears
    when a test asks for it.
    """
    payload = {
        "id": 12345,
        "credits": {"cast": cast or [], "crew": crew or []} if credits is None else credits,
        "videos": {"results": videos or []},
        "keywords": {"keywords": keywords or []},
    }
    if genres is not None:
        payload["genres"] = genres
    return payload


def _mock_bundle(payload: dict | None = None, status: int = 200) -> None:
    respx.get(f"{TMDB_API_URL}/movie/12345", params__contains={"append_to_response": "credits,videos,keywords"}).mock(
        return_value=httpx.Response(status, json=payload)
    )


@respx.mock
async def test_fetch_bundle_truncates_cast_to_top_8_comma_joined():
    _mock_bundle(_bundle_json(cast=[{"name": f"Actor {i}", "order": i} for i in range(12)]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.cast == ", ".join(f"Actor {i}" for i in range(8))


@respx.mock
async def test_fetch_bundle_joins_all_cast_when_fewer_than_8():
    _mock_bundle(_bundle_json(cast=[{"name": "Actor A", "order": 0}, {"name": "Actor B", "order": 1}]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.cast == "Actor A, Actor B"


@respx.mock
async def test_fetch_bundle_splits_crew_by_job():
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
    _mock_bundle(_bundle_json(crew=crew))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.directors == "Jane Doe"
    assert result.credits.producers == "John Smith, Alice"
    assert result.credits.writers == "Wanda"
    assert result.credits.composers == "Nino R."


@respx.mock
async def test_fetch_bundle_joins_multiple_composers():
    """Co-composed scores are real (Reznor/Ross, Carpenter/Lang) — ~6% of films."""
    crew = [_crew("Trent Reznor", "Original Music Composer"), _crew("Atticus Ross", "Original Music Composer")]
    _mock_bundle(_bundle_json(crew=crew))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.composers == "Trent Reznor, Atticus Ross"


@respx.mock
async def test_fetch_bundle_dedupes_person_credited_under_two_jobs():
    """TMDB lists a person once per job, so a Writer+Screenplay credit must not double up."""
    crew = [_crew("Ann Writer", "Writer"), _crew("Ann Writer", "Screenplay"), _crew("Zed", "Writer")]
    _mock_bundle(_bundle_json(crew=crew))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.writers == "Ann Writer, Zed"


@respx.mock
async def test_fetch_bundle_returns_none_fields_for_absent_roles():
    _mock_bundle(_bundle_json(crew=[_crew("Jane Doe", "Director")]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.directors == "Jane Doe"
    assert result.credits.cast is None
    assert result.credits.producers is None
    assert result.credits.writers is None
    assert result.credits.composers is None


@respx.mock
async def test_fetch_bundle_reads_the_five_territory_columns():
    """The territory fields ride the same payload as the credits — one request, eleven columns."""
    payload = _bundle_json(crew=[_crew("Jane Doe", "Director")]) | {
        "production_companies": [{"name": "A24"}, {"name": "Film4"}],
        "production_countries": [
            {"iso_3166_1": "US", "name": "United States of America"},
            {"iso_3166_1": "GB", "name": "United Kingdom"},
        ],
        "origin_country": ["US"],
        "spoken_languages": [
            {"iso_639_1": "en", "english_name": "English", "name": "English"},
            {"iso_639_1": "fr", "english_name": "French", "name": "Français"},
        ],
        "original_language": "en",
    }
    _mock_bundle(payload)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.studio == "A24, Film4"
    assert result.country == "United States of America, United Kingdom"
    # Codes, not names: TMDB ships no display names for origin_country anywhere.
    assert result.origin_country == "US"
    # english_name, not the `name` endonym — "French", never "Français".
    assert result.language == "English, French"
    assert result.original_language == "en"
    # Same payload still fills the credits.
    assert result.credits.directors == "Jane Doe"


@respx.mock
async def test_fetch_bundle_country_and_origin_country_can_disagree():
    """They are different fields, not two spellings — a co-production proves it.

    Measured on 400 real films: identical 73.7% of the time, never disjoint, and where
    they differ `origin` is the subset in 96 of 105 cases. `country` keeps the full
    co-production list because that is what the cache has always carried and what the
    taste backtest preferred.
    """
    payload = _bundle_json() | {
        "production_countries": [{"name": "China"}, {"name": "Hong Kong"}],
        "origin_country": ["HK"],
    }
    _mock_bundle(payload)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.country == "China, Hong Kong"
    assert result.origin_country == "HK"


@respx.mock
async def test_fetch_bundle_territory_columns_none_when_tmdb_has_none():
    """Absent lists are a film TMDB has no data for — normal, so None, not a raised error."""
    _mock_bundle(_bundle_json())
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.studio is None
    assert result.country is None
    assert result.origin_country is None
    assert result.language is None
    assert result.original_language is None


@respx.mock
async def test_fetch_bundle_skips_blank_territory_names():
    """A nameless entry must drop out of the join, not leave a dangling comma."""
    payload = _bundle_json() | {
        "production_companies": [{"name": "A24"}, {"name": None}, {"id": 7}],
        "spoken_languages": [{"iso_639_1": "en", "english_name": ""}, {"english_name": "Danish"}],
        "original_language": "",
    }
    _mock_bundle(payload)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.studio == "A24"
    assert result.language == "Danish"
    assert result.original_language is None


@respx.mock
async def test_fetch_bundle_survives_null_territory_lists():
    """A JSON `null` in an optional list must not take the required credits down with it.

    `default_factory` covers only an *absent* key, so without `NullableList` a payload
    carrying `"production_companies": null` raised ValidationError for the whole bundle —
    `_fetch_bundle` returned an empty `TmdbColumns()` and nulled `directors`/`cast` on a
    film whose credits parsed perfectly, logged as schema drift it isn't.
    """
    payload = _bundle_json(cast=[{"name": "Actor A", "order": 0}], crew=[_crew("Jane Doe", "Director")]) | {
        "production_companies": None,
        "production_countries": None,
        "origin_country": None,
        "spoken_languages": None,
    }
    _mock_bundle(payload)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.directors == "Jane Doe"
    assert result.credits.cast == "Actor A"
    # null, absent and [] all mean "TMDB has nothing on record" — one outcome, not three.
    assert result.studio is None
    assert result.country is None
    assert result.origin_country is None
    assert result.language is None


@respx.mock
async def test_fetch_bundle_still_flags_a_null_appended_block(caplog):
    """The null tolerance is scoped to the optional fields — a null `credits` is still drift."""
    _mock_bundle(_bundle_json() | {"credits": None})
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_fetch_bundle_returns_empty_when_tmdb_id_falsy():
    async with httpx.AsyncClient() as client:
        assert await _fetch_bundle(client, None, "fake-key") == TmdbColumns()
        assert await _fetch_bundle(client, "", "fake-key") == TmdbColumns()


async def test_fetch_bundle_returns_empty_when_api_key_empty():
    async with httpx.AsyncClient() as client:
        assert await _fetch_bundle(client, "12345", "") == TmdbColumns()


@respx.mock
async def test_fetch_bundle_returns_empty_on_http_error():
    _mock_bundle(status=404)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()


@respx.mock
async def test_fetch_bundle_tolerates_unknown_fields():
    """extra="ignore" — a real cast/crew entry carries ~20 fields we never read, and the
    bundle itself carries the whole movie detail payload beside the two appended blocks.
    """
    payload = _bundle_json(
        cast=[{"name": "Actor A", "order": 0, "gender": 2, "popularity": 12.3, "profile_path": "/x.jpg"}],
        crew=[{"name": "Jane Doe", "job": "Director", "department": "Directing", "credit_id": "abc123"}],
    )
    payload |= {"budget": 0, "adult": False, "spoken_languages": [{"iso_639_1": "en", "english_name": "English"}]}
    _mock_bundle(payload)
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.credits.cast == "Actor A"
    assert result.credits.directors == "Jane Doe"


@respx.mock
async def test_fetch_bundle_logs_warning_when_cast_member_missing_name(caplog):
    """A cast entry with no ``name`` at all is a shape change, not a normal cast gap
    (real TMDB entries always carry a name) — must log at WARNING and still return the
    safe empty ``TmdbColumns()`` fallback.
    """
    _mock_bundle(_bundle_json(cast=[{"order": 0}]))
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


@respx.mock
async def test_fetch_bundle_logs_warning_when_crew_is_wrong_type(caplog):
    """``directors`` is the taste ranker's highest-weighted dimension and the join's
    director confirmation — a ``crew`` field that stops being a list (e.g. TMDB starts
    sending an error string in its place) must be loud, not silently null every film.
    """
    _mock_bundle(_bundle_json(credits={"cast": [], "crew": "unexpected string, not a list"}))
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


@respx.mock
async def test_fetch_bundle_logs_warning_when_an_appended_block_is_missing(caplog):
    """``credits``/``videos`` are required on ``MovieBundle`` precisely so a dropped
    append block is loud: it would otherwise null six columns on every film at once.
    """
    respx.get(f"{TMDB_API_URL}/movie/12345", params__contains={"append_to_response": "credits,videos,keywords"}).mock(
        return_value=httpx.Response(200, json={"id": 12345, "credits": {"cast": [], "crew": []}})
    )
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


# ── _fetch_bundle: trailer half (async, httpx + respx) ──────────────────────────


@respx.mock
async def test_fetch_bundle_trailer_prefers_french_over_english():
    _mock_bundle(_bundle_json(videos=[_video("en-key", "en"), _video("fr-key", "fr")]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url == "https://www.youtube.com/watch?v=fr-key"


@respx.mock
async def test_fetch_bundle_trailer_falls_back_to_english_when_no_french():
    _mock_bundle(_bundle_json(videos=[_video("de-key", "de"), _video("en-key", "en")]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url == "https://www.youtube.com/watch?v=en-key"


@respx.mock
async def test_fetch_bundle_trailer_falls_back_to_other_language_when_no_fr_or_en():
    _mock_bundle(_bundle_json(videos=[_video("de-key", "de")]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url == "https://www.youtube.com/watch?v=de-key"


@respx.mock
async def test_fetch_bundle_trailer_excludes_unofficial_teaser_and_non_youtube():
    videos = [
        _video("unofficial-key", "fr", official=False),
        _video("teaser-key", "fr", video_type="Teaser"),
        _video("vimeo-key", "fr", site="Vimeo"),
    ]
    _mock_bundle(_bundle_json(videos=videos))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url is None


@respx.mock
async def test_fetch_bundle_trailer_returns_none_on_empty_results():
    _mock_bundle(_bundle_json())
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url is None


@respx.mock
async def test_fetch_bundle_trailer_tolerates_unknown_fields():
    """extra="ignore" — a real video entry carries fields (id, name, size, published_at, …) we never read."""
    videos = [
        {**_video("fr-key", "fr"), "id": "abc", "name": "Bande-annonce officielle", "size": 1080, "published_at": "2024-01-01"}
    ]
    _mock_bundle(_bundle_json(videos=videos))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.trailer_url == "https://www.youtube.com/watch?v=fr-key"


@respx.mock
async def test_fetch_bundle_logs_warning_when_video_results_wrong_type(caplog):
    """``results`` no longer being a list (e.g. TMDB starts sending a dict/error blob in
    its place) must log at WARNING and still return the safe empty fallback.
    """
    _mock_bundle({"id": 12345, "credits": {"cast": [], "crew": []}, "videos": {"results": "oops"}, "keywords": {"keywords": []}})
    with caplog.at_level(logging.WARNING, logger="modules.get_letterboxd_data"):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "tmdb_id=12345" in caplog.text


@respx.mock
async def test_fetch_bundle_requests_one_call_for_credits_and_videos():
    """The point of the consolidation: six columns, one request, and no ``language``
    parameter on it — ``language=fr-FR`` localises person names (see modules.tmdb).
    """
    route = respx.get(f"{TMDB_API_URL}/movie/12345").mock(
        return_value=httpx.Response(200, json=_bundle_json(crew=[_crew("Jane Doe", "Director")]))
    )
    async with httpx.AsyncClient() as client:
        await _fetch_bundle(client, "12345", "fake-key")
    assert route.call_count == 1
    params = route.calls[0].request.url.params
    assert params["append_to_response"] == "credits,videos,keywords"
    assert params["include_video_language"] == "fr,en,null"
    assert "language" not in params


# ── _fetch_bundle: taxonomy half (async, httpx + respx) ─────────────────────────


@respx.mock
async def test_fetch_bundle_reads_genres_and_keywords():
    """Both taxonomy columns ride the same payload as the credits — still one request."""
    _mock_bundle(
        _bundle_json(
            genres=[{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
            keywords=[{"id": 851, "name": "dual identity"}, {"id": 1541, "name": "nihilism"}],
        )
    )
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.genres == "Drama, Thriller"
    assert result.keywords == "dual identity, nihilism"


@respx.mock
async def test_fetch_bundle_dedupes_keywords():
    """TMDB can list one tag twice under different ids — the cache string must not.

    Unlike the territory lists (see ``_join_names``, deliberately un-deduped), the keyword
    block is crowd-maintained and duplicates do occur; a repeated value would be counted
    twice by any affinity built on the column.
    """
    _mock_bundle(_bundle_json(keywords=[{"id": 1, "name": "heist"}, {"id": 2, "name": "heist"}, {"id": 3, "name": "paris"}]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.keywords == "heist, paris"


@respx.mock
async def test_fetch_bundle_empty_taxonomy_is_none_not_empty_string():
    """A film with no genres and no tags yields None, the cache's "nothing on record" value."""
    _mock_bundle(_bundle_json())
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.genres is None
    assert result.keywords is None


@respx.mock
async def test_fetch_bundle_tolerates_a_null_genres_list():
    """``genres`` is a field nobody asked for, so a null must not fail the whole payload.

    Same NullableList contract as the territory lists: a null here would otherwise take the
    *required* credits down with it and null `directors` on a film that parsed fine.
    """
    _mock_bundle(_bundle_json(crew=[_crew("Jane Doe", "Director")]) | {"genres": None})
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.genres is None
    assert result.credits.directors == "Jane Doe"


@respx.mock
async def test_fetch_bundle_logs_warning_when_keywords_block_is_missing(caplog):
    """``keywords`` is a requested block, so its absence is drift — warning, not debug.

    The inner list may be empty (a film with no tags is ordinary); the wrapper may not.
    """
    _mock_bundle({"id": 12345, "credits": {"cast": [], "crew": []}, "videos": {"results": []}})
    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient() as client:
            result = await _fetch_bundle(client, "12345", "fake-key")
    assert result == TmdbColumns()
    assert "failed validation" in caplog.text


@respx.mock
async def test_fetch_bundle_empty_keywords_block_is_not_drift():
    """The mirror of the test above: an empty inner list is data, and must stay quiet."""
    _mock_bundle(_bundle_json(crew=[_crew("Jane Doe", "Director")]))
    async with httpx.AsyncClient() as client:
        result = await _fetch_bundle(client, "12345", "fake-key")
    assert result.keywords is None
    assert result.credits.directors == "Jane Doe"


# ── _fetch_all TMDB enrichment integration ──────────────────────────────────────


@respx.mock
async def test_fetch_all_attaches_tmdb_enrichment(mocker, make_movie):
    movie_mock = make_movie()
    movie_mock.tmdb_id = "42"
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=movie_mock)
    # Two routes on the same path, told apart by the parameters each call carries: the
    # French-locale title lookup, and the locale-free credits+videos bundle.
    title_route = respx.get(f"{TMDB_API_URL}/movie/42", params__contains={"language": "fr-FR"}).mock(
        return_value=httpx.Response(200, json={"title": "Titre Français"})
    )
    bundle_route = respx.get(f"{TMDB_API_URL}/movie/42", params__contains={"append_to_response": "credits,videos,keywords"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "credits": {
                    "cast": [{"name": "Actor A", "order": 0}],
                    "crew": [
                        _crew("Jane Doe", "Director"),
                        _crew("John Smith", "Producer"),
                        _crew("Wanda", "Screenplay"),
                        _crew("Nino R.", "Original Music Composer"),
                    ],
                },
                "videos": {"results": [_video("fr-key", "fr")]},
                "keywords": {"keywords": [{"name": "heist"}, {"name": "dual identity"}]},
                "genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
                "production_companies": [{"name": "A24"}],
                "production_countries": [{"iso_3166_1": "US", "name": "United States of America"}],
                "origin_country": ["US"],
                "spoken_languages": [{"iso_639_1": "en", "english_name": "English"}],
                "original_language": "en",
            },
        )
    )

    results = await _fetch_all(["some-slug"], api_key="fake-key")
    assert results[0] is not None
    assert results[0]["french_title"] == "Titre Français"
    assert results[0]["cast"] == "Actor A"
    assert results[0]["trailer_url"] == "https://www.youtube.com/watch?v=fr-key"
    # The crew columns come from the same bundle round-trip as the cast and the trailer.
    assert results[0]["directors"] == "Jane Doe"
    assert results[0]["producers"] == "John Smith"
    assert results[0]["writers"] == "Wanda"
    assert results[0]["composers"] == "Nino R."
    # …and so do the five territory columns and the two taxonomy ones, off the same payload.
    assert results[0]["studio"] == "A24"
    assert results[0]["country"] == "United States of America"
    assert results[0]["origin_country"] == "US"
    assert results[0]["language"] == "English"
    assert results[0]["original_language"] == "en"
    assert results[0]["genres"] == "Drama, Thriller"
    assert results[0]["keywords"] == "heist, dual identity"
    # Two requests per film, not three — the whole point of the bundle.
    assert (title_route.call_count, bundle_route.call_count) == (1, 1)


@respx.mock
async def test_fetch_all_ignores_letterboxd_values_tmdb_now_owns(mocker, make_movie):
    """TMDB's territory and taxonomy values always win over Letterboxd's, if Letterboxd
    happens to carry any: `_fetch_movie` already withholds them (see the withheld/dropped
    tests above), so this pins the *assignment* side of the same guarantee.
    """
    movie_mock = make_movie(
        genres=[{"type": "genre", "name": "Drame"}, {"type": "theme", "name": "Revenge"}],
        details=[{"type": "studio", "name": "Gaumont"}, {"type": "country", "name": "France"}],
    )
    movie_mock.tmdb_id = "42"
    mocker.patch("modules.get_letterboxd_data.Movie", return_value=movie_mock)
    respx.get(f"{TMDB_API_URL}/movie/42", params__contains={"language": "fr-FR"}).mock(
        return_value=httpx.Response(200, json={"title": "Titre Français"})
    )
    respx.get(f"{TMDB_API_URL}/movie/42", params__contains={"append_to_response": "credits,videos,keywords"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "credits": {"cast": [], "crew": [_crew("Jane Doe", "Director")]},
                "videos": {"results": []},
                "keywords": {"keywords": [{"name": "heist"}]},
                "genres": [{"id": 18, "name": "Drama"}],
                "production_companies": [{"name": "A24"}],
                "production_countries": [{"iso_3166_1": "US", "name": "United States of America"}],
                "origin_country": ["US"],
                "spoken_languages": [{"iso_639_1": "en", "english_name": "English"}],
                "original_language": "en",
            },
        )
    )

    results = await _fetch_all(["some-slug"], api_key="fake-key")
    assert results[0] is not None
    # TMDB wins, even though Letterboxd carried a value for the same column.
    assert results[0]["studio"] == "A24"
    assert results[0]["country"] == "United States of America"
    assert results[0]["genres"] == "Drama"
    assert results[0]["origin_country"] == "US"
    assert results[0]["original_language"] == "en"
    assert results[0]["keywords"] == "heist"
    # Themes stay Letterboxd's — TMDB's parallel vocabulary lands in keywords, not here.
    assert results[0]["themes"] == "Revenge"
    assert results[0]["directors"] == "Jane Doe"
    assert results[0]["french_title"] == "Titre Français"


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
    for fn in (_get_tmdb_movie, _get_tmdb_bundle):
        fn.retry.wait = wait_none()

    async with httpx.AsyncClient() as client:
        assert await _fetch_french_title(client, "42", "SUPERSECRETKEY") is None
        assert await _fetch_bundle(client, "42", "SUPERSECRETKEY") == TmdbColumns()

    assert caplog.records, "expected the failures to be logged at DEBUG"
    assert "api_key" in caplog.text, "expected the request URL in the log, else this test proves nothing"

    formatter = RedactingFormatter("%(message)s", secrets=["SUPERSECRETKEY"])
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert "SUPERSECRETKEY" not in rendered
    assert "***" in rendered
