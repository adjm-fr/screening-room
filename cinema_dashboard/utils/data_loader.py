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
    build_taste_profile(df)    -> compact taste summary string (affinity-ranked, see utils.taste)
    attach_streaming(df, …)    -> left-join FR streaming-providers cache by ``tmdb_id``
    future_showtimes(df)       -> rows with ``showtimes >= now`` in Europe/Paris (uncached)
"""

import logging
import unicodedata
from pathlib import Path

import pandas as pd
import streamlit as st
from common.parquet_io import read_parquet_validated
from contracts import SHOWTIMES
from modules.config import settings

from utils.streaming import STREAMING_COLUMNS, load_streaming_providers
from utils.taste import build_affinity, format_taste_profile

log = logging.getLogger(__name__)

DATA_TTL_SECONDS = 300

# Allocine emits naive screening times in Paris local wall-clock; future_showtimes
# anchors its "now" cutoff here so the dashboard is correct regardless of host tz.
SHOWTIMES_TZ = "Europe/Paris"


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


def _director_tokens(name: str) -> frozenset[str]:
    """Return the set of normalised name tokens for a single director.

    NFKD normalises accents; non-alpha chars become spaces (so hyphens,
    parenthetical disambiguators like ``"(II)"``, and dotted initials all
    split into tokens); everything is lower-cased. Returning an unordered
    *set* rather than a sorted string lets :func:`_directors_overlap` test
    token containment, which tolerates the name-form drift between sources
    (extra middle names, ``"Jr."``/``"(II)"`` suffixes) that exact-key
    equality could not.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalpha() else " " for c in s).lower()
    return frozenset(s.split())


def _director_key(name: str) -> str:
    """Return a canonical sort key for a single director name.

    NFKD normalises accents; non-alpha chars become spaces; tokens are sorted
    alphabetically so ``"Bong Joon-ho"`` and ``"Joon Ho Bong"`` both map to
    ``"bong ho joon"``.
    """
    return " ".join(sorted(_director_tokens(name)))


def _directors_overlap(allocine: str | float | None, letterboxd: str | float | None) -> bool:
    """True only when both director strings are present and share a name.

    A blank/NaN on *either* side means the title match can't be confirmed by
    director, so the row is rejected. This join is precision-first (inner, no
    left-join fallback): an unconfirmed title-only match lets a wrong film's
    screening attach to a watchlist entry for short or recurring French titles
    (remakes like ``"Nosferatu"`` or ``"Les Misérables"``). On real data the
    watchlist (TMDB) director field is effectively never blank and Allocine
    omits the director for ~0.6% of films, so requiring positive confirmation
    closes the wrong-attach hole at no measurable recall cost. An empty or
    whitespace-only string yields an empty token set and is rejected the same way.

    Confirmation uses **token containment**, not exact key equality: two names
    match when one's token set is a non-empty subset of the other's. This keeps
    legitimately-screening films that the two sources spell slightly
    differently — Allocine's ``"Kirk Jones (II)"`` vs TMDB's ``"Kirk Jones"``,
    ``"Ringo Lam"`` vs ``"Ringo Lam Ling-Tung"``, ``"Akinola Davies"`` vs
    ``"Akinola Davies Jr."`` — which the old sorted-key equality silently
    dropped. Genuinely different directors on a title collision (Murnau vs
    Eggers, Spielberg vs Haskin) share no containment relationship and are
    still rejected, so precision is preserved.
    """
    if pd.isna(allocine) or pd.isna(letterboxd):
        return False
    alloc_names = [t for n in str(allocine).split(" | ") if (t := _director_tokens(n))]
    lb_names = [t for n in str(letterboxd).split(", ") if (t := _director_tokens(n))]
    return any(a <= b or b <= a for a in alloc_names for b in lb_names)


def get_paths() -> tuple[Path | None, Path | None, Path | None]:
    """Resolve the three configured paths from environment variables.

    Returns a ``(movies_output, allocine_output, theaters_csv)`` tuple. Any
    element is ``None`` when the corresponding env var is unset, so callers
    can tailor an error message instead of failing on a generic ``KeyError``.
    """
    return (
        settings.movies_output_path,
        settings.allocine_output_path,
        settings.allocine_input_path,
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
    df = read_parquet_validated(showtimes_path, required_columns=SHOWTIMES.required_columns, label="showtimes")
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
    by director overlap (:func:`_directors_overlap`): a row is kept only when
    both sides carry director data and share a common name, so an unconfirmed
    title-only collision can't attach a wrong film's screening.
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
        "poster_url",
        "banner_url",
        "tmdb_id",
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
    """Derive a compact taste summary string from Letterboxd ratings.

    Thin formatter over the affinity profile in :mod:`utils.taste` — favourite
    (and least favourite) genres, themes, directors, and eras ranked by signed,
    shrunk affinity rather than raw mean rating. The string feeds the LLM
    system prompt via :func:`utils.chat.build_chat_context`.
    """
    if ratings_df.empty or "user_rating" not in ratings_df.columns:
        log.warning("Ratings DataFrame empty or missing user_rating — taste profile unavailable")
        return "No rating history available."
    return format_taste_profile(build_affinity(ratings_df))


def attach_streaming(df: pd.DataFrame, movies_output: str) -> pd.DataFrame:
    """Left-join the FR streaming-providers cache onto ``df`` by ``tmdb_id``.

    Adds one column per :data:`utils.streaming.STREAMING_COLUMNS` entry
    (``flatrate``, ``free`` — each ``list[str]`` of slugified provider names;
    empty lists for unmatched rows). Returns ``df`` with the columns present
    and empty when the cache is missing/empty or the input lacks ``tmdb_id``
    — pages can render unchanged in either case (graceful no-op).

    Not ``@st.cache_data``-decorated: the underlying
    :func:`utils.streaming.load_streaming_providers` already caches the read
    for 24h, the merge is cheap, and caching DataFrame arguments has its own
    hashing pitfalls.
    """
    out = df.copy()
    if "tmdb_id" not in out.columns or out.empty:
        for col in STREAMING_COLUMNS:
            out[col] = [[] for _ in range(len(out))]
        return out

    cache = load_streaming_providers(movies_output)
    if cache.empty:
        for col in STREAMING_COLUMNS:
            out[col] = [[] for _ in range(len(out))]
        return out

    keep = cache[["tmdb_id", *STREAMING_COLUMNS]].copy()
    keep["tmdb_id"] = keep["tmdb_id"].astype(str)
    out["tmdb_id"] = out["tmdb_id"].astype(str)
    out = out.merge(keep, on="tmdb_id", how="left")
    for col in STREAMING_COLUMNS:
        out[col] = out[col].apply(coerce_str_list)
    return out


def coerce_str_list(value: object) -> list[str]:
    """Coerce a possibly-NaN / numpy-array / list cell into ``list[str]``.

    Parquet list columns surface as Python lists or ``np.ndarray`` depending on
    the engine; unmatched left-join cells surface as ``None`` or scalar ``NaN``.
    Falsy elements are dropped so empty provider slugs never render.

    Single source of truth for the streaming-``flatrate`` coercion shared by the
    streaming badge renderer (:mod:`utils.ui`) and the calendar partition logic
    (``pages/calendar.py``). The scalar-``NaN`` check precedes the ``.tolist()``
    branch because numpy float scalars (e.g. ``np.float64('nan')``) are both
    ``float`` instances *and* expose ``.tolist()``.
    """
    if value is None:
        return []
    if isinstance(value, float) and pd.isna(value):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    if hasattr(value, "tolist"):
        return [str(v) for v in value.tolist() if v]  # type: ignore[union-attr]
    return []


def _now_paris() -> pd.Timestamp:
    """Current time anchored to Europe/Paris (tz-aware).

    Factored out so :func:`future_showtimes` has a single, mockable time source
    and the timezone choice is documented in one place.
    """
    return pd.Timestamp.now(tz=SHOWTIMES_TZ)


def future_showtimes(df: pd.DataFrame) -> pd.DataFrame:
    """Filter a showtimes DataFrame to rows in the future (Europe/Paris).

    Allocine emits screening times as naive ISO strings in **Paris local
    wall-clock** (no UTC offset), so the cutoff must be anchored to
    Europe/Paris — not the dashboard host's clock. Streamlit Community Cloud
    and most cloud regions run in UTC, where a naive ``pd.Timestamp.now()``
    would be off by the Paris↔UTC offset and leak already-finished screenings
    into the "upcoming" rails (or hide imminent ones).

    Robust to the ``showtimes`` column being tz-naive (the usual case) or
    tz-aware (should Allocine ever start emitting offsets): the cutoff is
    localised to match, so the comparison never raises ``Cannot compare
    tz-naive and tz-aware``.

    Intentionally **not** decorated with ``@st.cache_data``: the result depends
    on the current time and would otherwise stick to whatever ``now`` was when
    the cache entry was first written.
    """
    cutoff = _now_paris()
    if not isinstance(df["showtimes"].dtype, pd.DatetimeTZDtype):
        # Naive Paris wall-clock column → drop the tz so the comparison aligns.
        cutoff = cutoff.tz_localize(None)
    return df[df["showtimes"] >= cutoff]
