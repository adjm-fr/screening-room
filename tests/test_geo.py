"""Tests for utils.geo — geocoding cache logic with mocked Nominatim."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def theaters_csv(tmp_path: Path) -> Path:
    """Write a minimal three-row theaters CSV to a temp dir."""
    csv = tmp_path / "theaters.csv"
    csv.write_text(
        "C0073,Le Champo,51 rue des Ecoles 75005 Paris\n"
        "C0159,UGC Cine Cite Les Halles,7 Place de la Rotonde 75001 Paris\n"
        "C9999,No Address Theater,\n",
        encoding="utf-8",
    )
    return csv


@pytest.fixture
def patched_cache_path(tmp_path: Path, mocker):
    """Redirect utils.geo.GEO_CACHE_PATH to a temp file so tests don't touch the real cache."""
    cache = tmp_path / "theaters_geo.parquet"
    mocker.patch("utils.geo.GEO_CACHE_PATH", cache)
    return cache


def test_geocode_cache_miss_calls_nominatim_and_writes_cache(theaters_csv, patched_cache_path, mocker):
    fake_loc = mocker.MagicMock(latitude=48.85, longitude=2.34)
    geocode_mock = mocker.patch("geopy.geocoders.Nominatim.geocode", return_value=fake_loc)

    from utils.geo import load_geocoded_theaters

    df = load_geocoded_theaters(str(theaters_csv))

    # Two theaters have addresses → two geocode calls; the third (no address) is skipped.
    assert geocode_mock.call_count == 2
    assert patched_cache_path.exists()

    geocoded_rows = df[df["lat"].notna()]
    assert len(geocoded_rows) == 2
    assert (geocoded_rows["lat"] == 48.85).all()
    assert (geocoded_rows["lon"] == 2.34).all()


def test_geocode_cache_hit_skips_nominatim(theaters_csv, patched_cache_path, mocker):
    # Pre-seed the cache with both addressed theaters already geocoded.
    pd.DataFrame(
        [
            {"id": "C0073", "name": "Le Champo", "address": "51 rue des Ecoles 75005 Paris", "lat": 48.85, "lon": 2.34},
            {
                "id": "C0159",
                "name": "UGC Cine Cite Les Halles",
                "address": "7 Place de la Rotonde 75001 Paris",
                "lat": 48.86,
                "lon": 2.35,
            },
        ]
    ).to_parquet(patched_cache_path, index=False)

    geocode_mock = mocker.patch("geopy.geocoders.Nominatim.geocode")

    from utils.geo import load_geocoded_theaters

    df = load_geocoded_theaters(str(theaters_csv))

    # No new calls — both addresses are already cached.
    assert geocode_mock.call_count == 0
    assert df[df["id"] == "C0073"].iloc[0]["lat"] == 48.85
    assert df[df["id"] == "C0159"].iloc[0]["lon"] == 2.35


def test_geocode_failure_keeps_row_with_nan(theaters_csv, patched_cache_path, mocker):
    # Geocoder returns None for all addresses (e.g., no match found).
    mocker.patch("geopy.geocoders.Nominatim.geocode", return_value=None)

    from utils.geo import load_geocoded_theaters

    df = load_geocoded_theaters(str(theaters_csv))

    # All three rows still present — failures preserve the row with NaN coords.
    assert len(df) == 3
    assert df["lat"].isna().all()


def test_geocode_timeout_is_caught(theaters_csv, patched_cache_path, mocker):
    from geopy.exc import GeocoderTimedOut

    mocker.patch("geopy.geocoders.Nominatim.geocode", side_effect=GeocoderTimedOut("timeout"))

    from utils.geo import load_geocoded_theaters

    # Should not raise; failed rows just have NaN coords.
    df = load_geocoded_theaters(str(theaters_csv))
    assert len(df) == 3
    assert df["lat"].isna().all()
