"""
Shared data-loading helpers for the Streamlit dashboard.

Every page imports its parquet readers and the watchlist↔showtimes join from
this module. Streamlit's ``@st.cache_data`` keys on the qualified function
name plus its arguments — defining the loaders here (rather than per-page)
guarantees a single cache entry per parquet across all pages, so navigating
between pages is a cache hit until the TTL expires.

Public API:
    get_paths()                -> (movies_output, allocine_output, theaters_csv)
    load_watchlist(path)       -> watchlist DataFrame
    load_showtimes(path)       -> raw showtimes DataFrame (all dates)
    load_ratings(path)         -> Letterboxd ratings DataFrame
    load_letterboxd_cache(p)   -> full Letterboxd metadata cache
    build_watchlist_showtimes  -> inner-join of the two on movie title
    future_showtimes(df)       -> rows with ``showtimes >= now`` (uncached)
"""

import logging
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Loaded once at import time — pages no longer call load_dotenv themselves.
load_dotenv(Path(__file__).parents[1] / ".env")

log = logging.getLogger(__name__)

DATA_TTL_SECONDS = 300


def get_paths() -> tuple[Path | None, Path | None, Path | None]:
    """Resolve the three configured paths from environment variables.

    Returns a ``(movies_output, allocine_output, theaters_csv)`` tuple. Any
    element is ``None`` when the corresponding env var is unset, so callers
    can tailor an error message instead of failing on a generic ``KeyError``.
    """
    movies_raw = os.getenv("MOVIES_OUTPUT_PATH")
    allocine_raw = os.getenv("ALLOCINE_OUTPUT_PATH")
    theaters_raw = os.getenv("ALLOCINE_INPUT_PATH")
    return (
        Path(movies_raw) if movies_raw else None,
        Path(allocine_raw) if allocine_raw else None,
        Path(theaters_raw) if theaters_raw else None,
    )


# Loaders take ``str`` (not ``Path``) because @st.cache_data hashes args by
# value: passing the same string from every call site keeps the cache key
# stable across platforms and avoids subtle Path-equality edge cases.


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_watchlist(movies_output: str) -> pd.DataFrame:
    """Read ``watchlist_with_letterboxd.parquet`` from ``movies_output``."""
    log.debug("Loading watchlist from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "watchlist_with_letterboxd.parquet")
    log.info("Watchlist loaded: %d rows", len(df))
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_showtimes(showtimes_path: str) -> pd.DataFrame:
    """Read the Allocine ``showtimes.parquet`` file. Returns all rows;
    callers that only need upcoming screenings should pipe through
    :func:`future_showtimes`.
    """
    log.debug("Loading showtimes from %s", showtimes_path)
    df = pd.read_parquet(showtimes_path)
    n_theaters = df["theater_name"].nunique() if "theater_name" in df.columns else 0
    log.info("Showtimes loaded: %d rows, %d theaters", len(df), n_theaters)
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_ratings(movies_output: str) -> pd.DataFrame:
    """Read ``ratings_with_letterboxd.parquet`` from ``movies_output``."""
    log.debug("Loading ratings from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "ratings_with_letterboxd.parquet")
    log.info("Ratings loaded: %d rows", len(df))
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_letterboxd_cache(movies_output: str) -> pd.DataFrame:
    """Read the full Letterboxd metadata cache (``data_letterboxd.parquet``).

    This is the authoritative source for fields that may not have propagated
    into the ratings/watchlist parquets yet (e.g. ``runtime`` before
    enrichment). Currently used only by the Movies Database page.
    """
    log.debug("Loading letterboxd cache from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "data_letterboxd.parquet")
    log.info("Letterboxd cache loaded: %d rows", len(df))
    return df


def build_watchlist_showtimes(
    showtimes_df: pd.DataFrame,
    watchlist_df: pd.DataFrame,
) -> pd.DataFrame:
    """Inner-join showtimes with watchlist on movie title.

    Matching strategy:
    1. Primary: case-insensitive match between ``showtimes.movie`` and
       ``watchlist.title``.
    2. Fallback: for showtimes still unmatched, retry against
       ``watchlist.title`` using ``showtimes.original_title`` — this catches
       French theatrical releases listed under their localised title.

    Watchlist ``runtime`` and ``slug`` columns are renamed to
    ``runtime_minutes`` / ``letterboxd_slug`` before the merge so they don't
    clash with the scraper's own ``runtime`` column.
    """
    showtimes_df = showtimes_df.copy()
    watchlist_df = watchlist_df.copy()

    log.debug("Building watchlist-showtimes join: %d showtimes × %d watchlist entries", len(showtimes_df), len(watchlist_df))

    meta_cols = [c for c in ["slug", "title", "runtime", "genres", "letterboxd_avg_rating"] if c in watchlist_df.columns]
    wl_meta = watchlist_df[meta_cols].copy()
    wl_meta = wl_meta.rename(columns={"runtime": "runtime_minutes", "slug": "letterboxd_slug"})
    wl_meta["_key"] = wl_meta["title"].str.lower()

    showtimes_df["_key"] = showtimes_df["movie"].str.lower()
    merged = showtimes_df.merge(wl_meta.drop(columns=["title"]), on="_key", how="inner")
    log.debug("Primary title match: %d rows (%d unique movies)", len(merged), merged["_key"].nunique())

    if "original_title" in showtimes_df.columns:
        unmatched = showtimes_df[~showtimes_df["_key"].isin(merged["_key"])]
        if not unmatched.empty:
            unmatched = unmatched.copy()
            unmatched["_key"] = unmatched["original_title"].str.lower()
            fallback = unmatched.merge(wl_meta.drop(columns=["title"]), on="_key", how="inner")
            log.debug("Fallback original_title match: %d additional rows", len(fallback))
            merged = pd.concat([merged, fallback], ignore_index=True)

    n_movies = merged["movie"].nunique() if "movie" in merged.columns else 0
    log.info("Watchlist-showtimes join result: %d rows, %d unique movies", len(merged), n_movies)
    return merged.drop(columns=["_key"])


def future_showtimes(df: pd.DataFrame) -> pd.DataFrame:
    """Filter a showtimes DataFrame to rows in the future.

    Intentionally **not** decorated with ``@st.cache_data``: the result depends
    on ``pd.Timestamp.now()`` and would otherwise stick to whatever ``now``
    was when the cache entry was first written.
    """
    return df[df["showtimes"] >= pd.Timestamp.now()]
