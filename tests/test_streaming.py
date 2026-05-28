"""Tests for the Phase 2 TMDB streaming-providers data layer."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from utils.streaming import (
    TMDB_PROVIDERS_URL,
    _parse_fr,
    _slugify,
    _update_display_names_catalog,
    display_name,
    load_display_names_catalog,
    load_streaming_providers,
    refresh_streaming_providers,
)

# ── _slugify ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Netflix", "netflix"),
        ("MUBI", "mubi"),
        ("Canal+", "canalplus"),
        ("Disney Plus", "disneyplus"),
        ("Amazon Prime Video", "amazonprimevideo"),
        ("Arte", "arte"),
        ("  Apple TV  ", "appletv"),
    ],
)
def test_slugify(name, expected):
    assert _slugify(name) == expected


# ── display_name ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("slug", "expected", "catalogue"),
    [
        ("netflix", "Netflix", {"netflix": "Netflix"}),
        ("canalplus", "Canal+", {"canalplus": "Canal+"}),
        ("mubi", "MUBI", {"mubi": "MUBI"}),
        ("hbomax", "HBO Max", {"hbomax": "HBO Max"}),
        # Unknown slug: fall back to title-case with `plus` → `+`.
        ("someoddplus", "Someodd+", {}),
        ("randomprovider", "Randomprovider", {}),
    ],
)
def test_display_name(slug, expected, catalogue):
    assert display_name(slug, catalogue) == expected


# ── provider display-names catalogue ──────────────────────────────────────────


def test_update_catalogue_writes_new_entries(tmp_path):
    path = tmp_path / "catalog.json"
    added = _update_display_names_catalog({"hbomax": "HBO Max", "tf1plus": "TF1+"}, path=path)
    assert added == 2
    assert load_display_names_catalog(path) == {"hbomax": "HBO Max", "tf1plus": "TF1+"}


def test_update_catalogue_preserves_existing_entries(tmp_path):
    path = tmp_path / "catalog.json"
    # Pre-seed with a manual canonical capitalisation.
    _update_display_names_catalog({"mubi": "MUBI"}, path=path)
    # TMDB sends a different casing — we should not overwrite.
    added = _update_display_names_catalog({"mubi": "Mubi", "netflix": "Netflix"}, path=path)
    assert added == 1
    assert load_display_names_catalog(path) == {"mubi": "MUBI", "netflix": "Netflix"}


# ── _parse_fr ─────────────────────────────────────────────────────────────────


def _payload(fr: dict | None) -> dict:
    return {"results": {"FR": fr} if fr is not None else {}}


def test_parse_fr_full_payload():
    parsed = _parse_fr(
        _payload(
            {
                "link": "https://www.themoviedb.org/movie/1/watch?locale=FR",
                "flatrate": [{"provider_name": "MUBI"}, {"provider_name": "Netflix"}],
                "rent": [{"provider_name": "Apple TV"}],
                "buy": [{"provider_name": "Canal+"}],
            }
        )
    )
    assert parsed == {
        "flatrate": ["mubi", "netflix"],
        "tmdb_link": "https://www.themoviedb.org/movie/1/watch?locale=FR",
    }


def test_parse_fr_missing_fr_key_yields_empties():
    parsed = _parse_fr(_payload(None))
    assert parsed == {"flatrate": [], "tmdb_link": ""}


def test_parse_fr_skips_providers_without_name():
    parsed = _parse_fr(_payload({"flatrate": [{"provider_id": 8}, {"provider_name": "MUBI"}]}))
    assert parsed["flatrate"] == ["mubi"]


# ── refresh_streaming_providers ───────────────────────────────────────────────


@pytest.fixture
def movies_output(tmp_path, make_watchlist):
    """A movies_output dir containing a watchlist parquet with two tmdb_ids."""
    df = make_watchlist([{"tmdb_id": "101"}, {"tmdb_id": "202"}, {"tmdb_id": ""}, {"tmdb_id": None}])
    df.to_parquet(tmp_path / "watchlist_with_letterboxd.parquet", index=False)
    return str(tmp_path)


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "streaming.parquet"


@pytest.fixture(autouse=True)
def _isolate_display_names_catalog(tmp_path, mocker):
    """Redirect the on-disk provider-names catalogue into the test's tmp_path
    so refresh runs don't mutate the checked-in assets file."""
    mocker.patch("utils.streaming.PROVIDER_DISPLAY_NAMES_PATH", tmp_path / "provider_display_names.json")


def _route(mock, tmdb_id: str, *, status: int = 200, fr: dict | None = None) -> respx.Route:
    return mock.get(TMDB_PROVIDERS_URL.format(tmdb_id=tmdb_id)).mock(
        return_value=httpx.Response(status, content=json.dumps(_payload(fr)))
    )


def test_no_api_key_is_a_noop(movies_output, cache_path):
    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key=None, cache_path=cache_path)
    assert summary == {"skipped": True}
    assert not cache_path.exists()


@respx.mock
def test_refresh_writes_expected_schema_and_rows(movies_output, cache_path):
    _route(respx, "101", fr={"flatrate": [{"provider_name": "MUBI"}], "link": "L"})
    _route(respx, "202", fr={"flatrate": [{"provider_name": "MUBI"}], "link": "L"})

    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path)

    assert summary == {"fetched": 2, "skipped_fresh": 0, "errors": 0, "new_provider_names": 1}
    assert respx.calls.call_count == 2  # only the two non-empty tmdb_ids
    df = pd.read_parquet(cache_path)
    assert set(df.columns) == {"tmdb_id", "flatrate", "tmdb_link", "fetched_at"}
    assert sorted(df["tmdb_id"]) == ["101", "202"]
    assert df.iloc[0]["flatrate"].tolist() == ["mubi"]


@respx.mock
def test_incremental_skip_and_force(movies_output, cache_path):
    fresh = datetime.now(UTC) - timedelta(days=1)
    pd.DataFrame(
        [
            {"tmdb_id": "101", "flatrate": ["mubi"], "tmdb_link": "L", "fetched_at": fresh},
            {"tmdb_id": "202", "flatrate": [], "tmdb_link": "", "fetched_at": fresh},
        ]
    ).to_parquet(cache_path, index=False)

    _route(respx, "101", fr={"flatrate": [{"provider_name": "Netflix"}]})
    _route(respx, "202", fr={"flatrate": [{"provider_name": "Netflix"}]})

    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path)
    assert summary == {"fetched": 0, "skipped_fresh": 2, "errors": 0, "new_provider_names": 0}
    assert respx.calls.call_count == 0

    forced = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path, force=True)
    assert forced == {"fetched": 2, "skipped_fresh": 0, "errors": 0, "new_provider_names": 1}
    assert respx.calls.call_count == 2


@respx.mock
def test_stale_row_is_refetched(movies_output, cache_path):
    stale = datetime.now(UTC) - timedelta(days=30)
    pd.DataFrame([{"tmdb_id": "101", "flatrate": [], "tmdb_link": "", "fetched_at": stale}]).to_parquet(cache_path, index=False)

    _route(respx, "101", fr={"flatrate": [{"provider_name": "MUBI"}]})
    _route(respx, "202", fr={"flatrate": [{"provider_name": "MUBI"}]})

    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path)
    assert summary["fetched"] == 2 and summary["skipped_fresh"] == 0


@respx.mock
def test_one_request_failure_does_not_abort_batch(movies_output, cache_path):
    respx.get(TMDB_PROVIDERS_URL.format(tmdb_id="101")).mock(side_effect=httpx.ConnectError("boom"))
    _route(respx, "202", fr={"flatrate": [{"provider_name": "MUBI"}]})

    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path)

    assert summary == {"fetched": 1, "skipped_fresh": 0, "errors": 1, "new_provider_names": 1}
    df = pd.read_parquet(cache_path)
    assert df["tmdb_id"].tolist() == ["202"]  # the failed "101" is absent


@respx.mock
def test_non_200_counts_as_error(movies_output, cache_path):
    _route(respx, "101", status=404)
    _route(respx, "202", status=404)

    summary = refresh_streaming_providers(movies_output=movies_output, tmdb_api_key="key", cache_path=cache_path)
    assert summary["errors"] == 2 and summary["fetched"] == 0


# ── load_streaming_providers ──────────────────────────────────────────────────


def test_load_returns_empty_typed_frame_when_cache_missing(mocker):
    mocker.patch("utils.streaming.STREAMING_CACHE_PATH", Path("/nonexistent/streaming.parquet"))
    df = load_streaming_providers("ignored")
    assert df.empty
    assert list(df.columns) == ["tmdb_id", "flatrate", "tmdb_link", "fetched_at"]
