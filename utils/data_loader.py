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
    build_watchlist_showtimes  -> join of watchlist and showtimes
    build_taste_profile(df)    -> compact taste summary string (top genres + directors by avg rating)
    future_showtimes(df)       -> rows with ``showtimes >= now`` (uncached)
"""

import logging
import os
import unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Loaded once at import time — pages no longer call load_dotenv themselves.
load_dotenv(Path(__file__).parents[1] / ".env")

log = logging.getLogger(__name__)

DATA_TTL_SECONDS = 300


def _normalize_title(raw: object) -> str:
    """Return a canonical form of a title for join-key matching.

    Strips accents, lowercases, replaces non-alphanumeric chars with spaces
    (digits are preserved — ``2001``, ``Blade Runner 2049``), then collapses
    whitespace. Returns an empty string for null/empty input so that merge
    keys stay string-typed.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not isinstance(raw, str) or not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s).lower()
    return " ".join(s.split())


def _director_key(name: str) -> str:
    """Return a canonical sort key for a single director name.

    NFKD normalises accents; non-alpha chars become spaces; tokens are sorted
    alphabetically so ``"Bong Joon-ho"`` and ``"Joon Ho Bong"`` both map to
    ``"bong ho joon"``.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalpha() else " " for c in s).lower()
    return " ".join(sorted(s.split()))


def _directors_overlap(allocine: str | None, letterboxd: str | None) -> bool:
    """True when the two director strings share at least one normalised name.

    Returns True when either value is null/NaN — a missing director field
    means we can't confirm a mismatch, so we keep the title-matched row.
    """
    if pd.isna(allocine) or pd.isna(letterboxd):
        return True
    alloc_keys = {_director_key(n.strip()) for n in str(allocine).split(" | ") if n.strip()}
    lb_keys = {_director_key(n.strip()) for n in str(letterboxd).split(", ") if n.strip()}
    return bool(alloc_keys & lb_keys)


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
def load_watchlist(movies_output: str) -> pd.DataFrame:  # pragma: no cover
    """Read ``watchlist_with_letterboxd.parquet`` from ``movies_output``."""
    log.debug("Loading watchlist from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "watchlist_with_letterboxd.parquet")
    log.info("Watchlist loaded: %d rows", len(df))
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_showtimes(showtimes_path: str) -> pd.DataFrame:  # pragma: no cover
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
def load_ratings(movies_output: str) -> pd.DataFrame:  # pragma: no cover
    """Read ``ratings_with_letterboxd.parquet`` from ``movies_output``."""
    log.debug("Loading ratings from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "ratings_with_letterboxd.parquet")
    log.info("Ratings loaded: %d rows", len(df))
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def load_letterboxd_cache(movies_output: str) -> pd.DataFrame:  # pragma: no cover
    """Read the full Letterboxd metadata cache (``data_letterboxd.parquet``).

    This is the authoritative source for fields that may not have propagated
    into the ratings/watchlist parquets yet (e.g. ``runtime`` before
    enrichment). Currently used only by the Movies Database page.
    """
    log.debug("Loading letterboxd cache from %s", movies_output)
    df = pd.read_parquet(Path(movies_output) / "data_letterboxd.parquet")
    log.info("Letterboxd cache loaded: %d rows", len(df))
    return df


@st.cache_data(ttl=DATA_TTL_SECONDS)
def build_watchlist_showtimes(
    showtimes_df: pd.DataFrame,
    watchlist_df: pd.DataFrame,
) -> pd.DataFrame:
    """Join showtimes with the watchlist on French title, confirmed by director.

    Normalises Allocine's ``movie`` (French display title) against the
    watchlist's TMDB ``french_title`` when present, falling back to the
    watchlist's display ``title``. Normalisation strips accents, punctuation,
    and case via :func:`_normalize_title`. Title-matched rows are then filtered
    by director overlap (:func:`_directors_overlap`): rows where both sides
    carry director data but share no common name are dropped.
    """
    showtimes_df = showtimes_df.copy().reset_index(drop=True)
    showtimes_df["_st_idx"] = showtimes_df.index

    log.debug(
        "Building watchlist-showtimes join: %d showtimes × %d watchlist entries",
        len(showtimes_df),
        len(watchlist_df),
    )

    _want_cols = [
        "slug",
        "title",
        "french_title",
        "runtime",
        "genres",
        "letterboxd_avg_rating",
        "directors",
        "release_year",
    ]
    meta_cols = [c for c in _want_cols if c in watchlist_df.columns]
    wl_meta = watchlist_df[meta_cols].copy()
    wl_meta = wl_meta.rename(columns={"runtime": "runtime_minutes", "slug": "letterboxd_slug", "title": "letterboxd_title"})

    showtimes_df["_key"] = showtimes_df["movie"].map(_normalize_title)
    if "french_title" in wl_meta.columns:
        wl_key = wl_meta["french_title"].fillna(wl_meta["letterboxd_title"]).map(_normalize_title)
    else:
        wl_key = wl_meta["letterboxd_title"].map(_normalize_title)
    wl_meta["_key"] = wl_key

    pass1 = showtimes_df.merge(wl_meta, on="_key", how="inner")
    log.debug("Pass 1 (French title) matched %d rows (before director filter)", len(pass1))

    if "director" in pass1.columns and "directors" in pass1.columns and not pass1.empty:
        pass1 = pass1[pass1.apply(lambda r: _directors_overlap(r["director"], r["directors"]), axis=1)]

    log.debug("Pass 1 kept %d rows after director filter", len(pass1))

    merged = pass1.copy()

    if "_st_idx" in merged.columns and "letterboxd_slug" in merged.columns:
        merged = merged.drop_duplicates(subset=["_st_idx", "letterboxd_slug"])
    elif "_st_idx" in merged.columns:
        merged = merged.drop_duplicates(subset=["_st_idx"])

    drop_cols = [
        c
        for c in [
            "_key",
            "_st_idx",
            "director",
            "original_title",
            "runtime",
            "letterboxd_slug",
            "french_title",
            "release_year_x",
            "release_year_y",
        ]
        if c in merged.columns
    ]
    merged = merged.drop(columns=drop_cols).rename(columns={"movie": "french_title"})
    n_movies = merged["french_title"].nunique() if "french_title" in merged.columns else 0
    log.info("Watchlist-showtimes join result: %d rows, %d unique movies", len(merged), n_movies)
    return merged


@st.cache_data(ttl=DATA_TTL_SECONDS)
def build_taste_profile(ratings_df: pd.DataFrame) -> str:
    """Derive a compact taste summary from Letterboxd ratings.

    Computes top genres and directors by average user rating. Cached so the
    groupby/explode runs once regardless of which page loads it first.
    """
    if ratings_df.empty or "user_rating" not in ratings_df.columns:
        log.warning("Ratings DataFrame empty or missing user_rating — taste profile unavailable")
        return "No rating history available."

    avg = ratings_df["user_rating"].mean()
    lines = [f"Average rating given: {avg:.1f}/5"]

    if "genres" in ratings_df.columns:
        exploded = (
            ratings_df[["genres", "user_rating"]].dropna().assign(genre=lambda d: d["genres"].str.split(", ")).explode("genre")
        )
        top_genres = exploded.groupby("genre")["user_rating"].mean().sort_values(ascending=False).head(5).index.tolist()
        lines.append(f"Favourite genres: {', '.join(top_genres)}")

    if "directors" in ratings_df.columns:
        exploded_dir = (
            ratings_df[["directors", "user_rating"]]
            .dropna()
            .assign(director=lambda d: d["directors"].str.split(", "))
            .explode("director")
        )
        top_dirs = (
            exploded_dir.groupby("director")["user_rating"]
            .agg(["mean", "count"])
            .query("count >= 2")
            .sort_values("mean", ascending=False)
            .head(5)
            .index.tolist()
        )
        if top_dirs:
            lines.append(f"Favourite directors (≥2 films rated): {', '.join(top_dirs)}")

    profile = "\n".join(lines)
    log.debug("Taste profile:\n%s", profile)
    return profile


def future_showtimes(df: pd.DataFrame) -> pd.DataFrame:
    """Filter a showtimes DataFrame to rows in the future.

    Intentionally **not** decorated with ``@st.cache_data``: the result depends
    on ``pd.Timestamp.now()`` and would otherwise stick to whatever ``now``
    was when the cache entry was first written.
    """
    return df[df["showtimes"] >= pd.Timestamp.now()]
