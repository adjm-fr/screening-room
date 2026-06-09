"""
Geocoding and map rendering for theaters.

Theater addresses live in ``theaters.csv`` as plain text. This module geocodes
each address once via Nominatim and persists the lat/lon on disk so the second
session-load is a free cache hit. The map renderer uses ``st.pydeck_chart``
(Streamlit-native, no extra dep) which is GPU-accelerated and looks more
contemporary than the Folium/Leaflet defaults.

Public API:
    load_geocoded_theaters(theaters_csv) -> DataFrame[id, name, address, lat, lon]
    render_theater_map(df, *, count_col=None, popup_col=None) -> None

Cache file: ``data/theaters_geo.parquet`` — already covered by ``*.parquet``
and ``data/`` rules in ``.gitignore``. Refresh = delete the file (no flag,
per the project's "no defensive cache guards" rule).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

log = logging.getLogger(__name__)

NOMINATIM_USER_AGENT = "cinema_dashboard/1.0 (github.com/adjm-fr)"
GEO_CACHE_PATH = Path("data") / "theaters_geo.parquet"
PARIS_LAT = 48.8566
PARIS_LON = 2.3522


def _read_theaters_csv(theaters_csv: str | Path) -> pd.DataFrame:
    """Read the headerless three-column theaters CSV into a DataFrame."""
    return pd.read_csv(theaters_csv, header=None, names=["id", "name", "address"], dtype=str).fillna("")


def _read_cache() -> pd.DataFrame:
    """Read the geocode cache; return an empty DataFrame with the right schema if missing."""
    if GEO_CACHE_PATH.exists():
        return pd.read_parquet(GEO_CACHE_PATH)
    return pd.DataFrame(columns=["id", "name", "address", "lat", "lon"]).astype(
        {"id": "string", "name": "string", "address": "string", "lat": "float64", "lon": "float64"}
    )


def _write_cache(df: pd.DataFrame) -> None:
    GEO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(GEO_CACHE_PATH, index=False)


@st.cache_data(ttl=86400)
def load_geocoded_theaters(theaters_csv: str) -> pd.DataFrame:  # pragma: no cover
    """Return theaters with lat/lon, geocoding any uncached address via Nominatim.

    Cached at the Streamlit level for 24h; persisted to disk so cold starts
    skip the geocoder entirely once an address has been resolved. Geocoder
    failures are logged and produce ``NaN`` coords — the row is kept so it
    still appears in tables, just not on the map.
    """
    theaters_df = _read_theaters_csv(theaters_csv)
    cache = _read_cache()

    # Identify rows whose (id, address) are not yet in the cache.
    cache_keys = set(zip(cache["id"], cache["address"], strict=False))
    to_geocode = theaters_df[
        ~theaters_df.apply(lambda r: (r["id"], r["address"]) in cache_keys, axis=1) & (theaters_df["address"] != "")
    ]

    if to_geocode.empty:
        log.debug("Geo cache hit for all %d theaters", len(theaters_df))
        return theaters_df.merge(cache[["id", "lat", "lon"]], on="id", how="left")

    log.info("Geocoding %d new address(es) via Nominatim", len(to_geocode))
    geolocator = Nominatim(user_agent=NOMINATIM_USER_AGENT)
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

    new_rows: list[dict] = []
    for _, row in to_geocode.iterrows():
        try:
            location = geocode(row["address"], timeout=10)
        except (GeocoderTimedOut, GeocoderServiceError) as exc:
            log.warning("Geocoding failed for %r: %s", row["address"], exc)
            location = None
        new_rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "address": row["address"],
                "lat": location.latitude if location else float("nan"),
                "lon": location.longitude if location else float("nan"),
            }
        )

    updated_cache = pd.concat([cache, pd.DataFrame(new_rows)], ignore_index=True)
    _write_cache(updated_cache)
    return theaters_df.merge(updated_cache[["id", "lat", "lon"]], on="id", how="left")


def render_theater_map(
    theaters_df: pd.DataFrame,
    *,
    count_col: str | None = None,
    popup_col: str | None = None,
    height: int = 480,
) -> None:
    """Render a pydeck ScatterplotLayer over Paris.

    Rows with NaN lat/lon are silently dropped. ``count_col`` (when present)
    sizes markers via a sqrt scale so a 10× count visually reads as ~3×.
    """
    plot_df = theaters_df.dropna(subset=["lat", "lon"]).copy()
    if plot_df.empty:
        st.info("No mappable theater coordinates available yet.")
        return

    if count_col and count_col in plot_df.columns:
        counts = pd.to_numeric(plot_df[count_col], errors="coerce").fillna(0)
        plot_df["_radius"] = 80 + counts.pow(0.5) * 40
    else:
        plot_df["_radius"] = 120

    if popup_col and popup_col in plot_df.columns:
        plot_df["_tooltip"] = plot_df[popup_col].astype(str)
    else:
        plot_df["_tooltip"] = plot_df.get("name", pd.Series("", index=plot_df.index)).astype(str)

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=plot_df,
        get_position="[lon, lat]",
        get_radius="_radius",
        get_fill_color=[230, 57, 70, 200],
        pickable=True,
        radius_min_pixels=4,
        radius_max_pixels=40,
    )
    view_state = pdk.ViewState(latitude=PARIS_LAT, longitude=PARIS_LON, zoom=11.5, pitch=0)
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip={"text": "{_tooltip}"},
            map_style="dark",
        ),
        height=height,
    )
