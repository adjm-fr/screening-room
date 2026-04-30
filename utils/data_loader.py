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
    build_watchlist_showtimes  -> inner-join of watchlist and showtimes on original title
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


def _normalize_directors(raw: str | None, sep: str) -> set[str]:
    """Return a set of normalised director name tokens.

    For each name: strips accents, lowercases, removes non-alpha chars, and
    sorts the tokens within the name (handles first/last name order swaps).
    """
    if not raw:
        return set()
    names = []
    for part in raw.split(sep):
        part = part.strip()
        part = unicodedata.normalize("NFKD", part)
        part = "".join(c for c in part if not unicodedata.combining(c))
        part = "".join(c if c.isalpha() else " " for c in part).lower()
        tokens = sorted(part.split())
        if tokens:
            names.append(" ".join(tokens))
    return set(names)


def _directors_overlap(allocine: str | None, letterboxd: str | None) -> bool:
    """True when the two director strings share at least one normalised name.

    Returns True when either value is null/NaN — a missing director field
    means we can't confirm a mismatch, so we keep the title-matched row.
    """
    if pd.isna(allocine) or pd.isna(letterboxd):
        return True
    return bool(
        _normalize_directors(allocine, sep=" | ") & _normalize_directors(letterboxd, sep=", ")
    )


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


@st.cache_data(ttl=DATA_TTL_SECONDS)
def build_watchlist_showtimes(
    showtimes_df: pd.DataFrame,
    watchlist_df: pd.DataFrame,
) -> pd.DataFrame:
    """Inner-join showtimes with watchlist on a canonical title key.

    Join key — same rule on both sides:
        ``original_title`` when present, else the display title
        (Allocine ``movie`` / Letterboxd ``title``).
    When Letterboxd omits ``original_title`` it means the display title IS
    the original, so the fallback is correct in both directions.

    After matching, rows are further filtered by director overlap when both
    sources carry director data: at least one normalised director name must
    appear in both sets (handles co-director ordering and minor typographic
    differences). Rows where either side lacks a director field are kept as-is.

    Output columns: ``french_title`` (Allocine display title), ``letterboxd_title``
    (Letterboxd display title), ``original_title`` (Letterboxd true original title),
    ``theater_id``, ``theater_name``, ``showtimes``, ``is_weekend``,
    ``runtime_minutes``, ``genres``, ``letterboxd_avg_rating``, ``directors``.
    Allocine-only columns (``director``, ``runtime``) and ``letterboxd_slug``
    are dropped after the join.
    """
    showtimes_df = showtimes_df.copy()

    log.debug("Building watchlist-showtimes join: %d showtimes × %d watchlist entries", len(showtimes_df), len(watchlist_df))

    meta_cols = [c for c in ["slug", "title", "original_title", "runtime", "genres", "letterboxd_avg_rating", "directors"] if c in watchlist_df.columns]
    wl_meta = watchlist_df[meta_cols].copy()
    wl_meta = wl_meta.rename(columns={"runtime": "runtime_minutes", "slug": "letterboxd_slug", "title": "letterboxd_title"})

    # Key: original_title when present (it IS the canonical title), else the display title.
    # Allocine's original_title is consumed into _key and dropped so it doesn't clash
    # with wl_meta's original_title column during the merge.
    showtimes_df["_key"] = showtimes_df.get("original_title", showtimes_df["movie"]).fillna(showtimes_df["movie"]).str.lower()
    showtimes_df = showtimes_df.drop(columns=["original_title"], errors="ignore")

    wl_orig = wl_meta.get("original_title", wl_meta["letterboxd_title"]) if "original_title" in wl_meta.columns else wl_meta["letterboxd_title"]
    wl_meta["_key"] = wl_orig.fillna(wl_meta["letterboxd_title"]).str.lower()

    use_director_filter = "director" in showtimes_df.columns and "directors" in wl_meta.columns

    merged = showtimes_df.merge(wl_meta, on="_key", how="inner")
    if use_director_filter and not merged.empty:
        merged = merged[merged.apply(lambda r: _directors_overlap(r["director"], r["directors"]), axis=1)]
    log.debug("Title match: %d rows (%d unique movies)", len(merged), merged["_key"].nunique())

    drop_cols = [c for c in ["_key", "director", "runtime", "letterboxd_slug"] if c in merged.columns]
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
