"""
Allocine → Letterboxd cache enrichment.

Resolves Allocine film tuples (title, original_title, director, release_year) to
Letterboxd slugs and expands data_letterboxd.parquet to cover every film in a
showtimes parquet, not only the user's watchlist and ratings.
"""

import asyncio
import logging
import os
import unicodedata

import pandas as pd
from common.parquet_io import read_parquet_validated, write_parquet_validated
from contracts import DATA_LETTERBOXD, SHOWTIMES
from letterboxdpy.search import Search, SearchFilter
from tenacity import retry, stop_after_attempt, wait_exponential

from modules.config import TmdbColumnGroup
from modules.get_letterboxd_data import get_letterboxd_data

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def _search_films(query: str) -> list[dict]:
    """Run a Letterboxd film search, retrying on transient failures."""
    return Search(query, SearchFilter.FILMS).results.get("results", [])


def _director_tokens(name: str) -> frozenset[str]:
    """Return the set of normalised name tokens for a single director.

    NFKD normalises accents; non-alpha chars become spaces (so hyphens, dotted
    initials, and parenthetical suffixes like ``"(II)"`` all split into
    tokens); everything is lower-cased. A token *set* (not a joined string)
    lets :func:`_directors_overlap` test containment, which tolerates the
    name-form drift between Allocine and Letterboxd that exact-string
    equality could not — e.g. Allocine's ``"S.S. Rajamouli"`` vs
    Letterboxd's ``"S. S. Rajamouli"``.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalpha() else " " for c in s).lower()
    return frozenset(s.split())


def _split_director_tokens(value: object, sep: str) -> list[frozenset[str]]:
    """Split a director string on ``sep`` into one token set per director.

    ``isinstance`` (not just truthiness) guards against a NaN ``directors``
    cell from a real cache DataFrame — ``bool(float("nan"))`` is ``True``, so
    a plain ``if not value`` check lets it through to ``.split()`` and crashes.
    """
    if not isinstance(value, str) or not value:
        return []
    return [tokens for name in value.split(sep) if (tokens := _director_tokens(name))]


def _letterboxd_result_director_tokens(item: dict) -> list[frozenset[str]]:
    """Extract one token set per director from a Letterboxd search result."""
    return [tokens for d in item.get("directors") or [] if (name := d.get("name")) and (tokens := _director_tokens(name))]


def _directors_overlap(a_tokens: list[frozenset[str]], b_tokens: list[frozenset[str]]) -> bool:
    """True when any director on one side is a token-subset (or superset) of one on the other.

    Containment, not equality, is what tolerates the name-form drift between
    sources (extra middle names, ``"Jr."``/``"(II)"`` suffixes, dotted-initial
    spacing) — see :func:`_director_tokens`.
    """
    return any(a <= b or b <= a for a in a_tokens for b in b_tokens)


def _search_letterboxd_slug(query: str, year_str: str | None, director: str | None) -> str | None:
    """Search Letterboxd for a film slug, scoring candidates by year and director match."""

    if not year_str or not director:
        logger.debug("Letterboxd search skipped: query=%r year=%s director=%s", query, year_str, director)
        return None

    logger.debug("Letterboxd search: query=%r year=%s director=%s", query, year_str, director)
    try:
        results = _search_films(query)
    except Exception as e:
        logger.debug("Letterboxd search failed for query=%r: %s", query, e)
        return None

    logger.debug("  → %d candidates for query=%r", len(results), query)
    allocine_tokens = _split_director_tokens(director, "|")

    for item in results:
        item_year = str(item.get("year", ""))
        if item_year != year_str:
            logger.debug("    skip slug=%s: year %s ≠ %s", item.get("slug"), item_year, year_str)
            continue
        lb_tokens = _letterboxd_result_director_tokens(item)
        if _directors_overlap(allocine_tokens, lb_tokens):
            slug = item.get("slug") or None
            logger.debug("    match slug=%s (directors %s)", slug, allocine_tokens)
            return slug
        logger.debug("    skip slug=%s: no director overlap (%s vs %s)", item.get("slug"), allocine_tokens, lb_tokens)

    return None


async def resolve_slug_from_allocine_tuple(
    title: str,
    original_title: str | None,
    director: str | None,
    release_year: int | str | None,
) -> str | None:
    """Resolve a Letterboxd slug from an Allocine film tuple.

    Strategy:
    1. Search Letterboxd by ``title``, post-filter candidates by year and director.
    2. Fall back to ``original_title`` if the first search yields nothing.

    Films that can't be resolved against Letterboxd are dropped from downstream
    processing — there is no TMDB fallback.

    The blocking Letterboxd ``Search`` call is offloaded to a worker thread via
    ``asyncio.to_thread`` so a batch of resolutions can run concurrently from
    a single event loop.

    Args:
        title: French display title from Allocine.
        original_title: Original-language title (may be None or identical to title).
        director: Director name string from Allocine (used for post-filtering).
        release_year: 4-digit release year (int or str).  May be None.

    Returns:
        A Letterboxd slug string, or ``None`` if resolution failed.
    """
    try:
        year_str = str(int(release_year)) if release_year else None
    except (TypeError, ValueError):
        year_str = str(release_year) if release_year else None

    slug = await asyncio.to_thread(_search_letterboxd_slug, title, year_str, director)
    if not slug and original_title and original_title != title:
        logger.debug("title miss for %r, trying original_title=%r", title, original_title)
        slug = await asyncio.to_thread(_search_letterboxd_slug, original_title, year_str, director)

    logger.debug("resolved %r → %s", title, slug)
    return slug


async def _resolve_all_slugs(films: list[dict], concurrency: int = 10) -> list[str | None]:
    """Resolve a batch of Allocine film tuples to Letterboxd slugs concurrently.

    Mirrors the ``_fetch_all`` shape in ``get_letterboxd_data``: a semaphore caps
    in-flight searches, a TaskGroup awaits all tasks, and progress is logged every
    50 completions.
    """
    sem = asyncio.Semaphore(concurrency)
    total = len(films)
    done = 0
    results: list[str | None] = [None] * total

    async def _guarded(i: int, film: dict) -> None:
        nonlocal done
        async with sem:
            results[i] = await resolve_slug_from_allocine_tuple(
                film["title"], film["original_title"], film["director"], film["release_year"]
            )
        done += 1
        if done % 50 == 0 or done == total:
            logger.info("Resolved %d/%d", done, total)

    async with asyncio.TaskGroup() as tg:
        for i, film in enumerate(films):
            tg.create_task(_guarded(i, film))

    return results


def _normalize_title(raw: object) -> str:
    """Return a canonical form of a title for cache join-key matching.

    Strips accents, lowercases, replaces non-alphanumeric chars with spaces,
    then collapses whitespace. Returns an empty string for null/non-str input.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s).lower()
    return " ".join(s.split())


def _build_cache_index(cache_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Index the existing cache by normalised title for cache-first matching.

    Maps each normalised ``title``/``french_title``/``original_title`` to the
    cache rows that carry it, so a showtimes film can be matched directly
    against ``data_letterboxd.parquet`` before ever hitting Letterboxd — a
    film already in the cache under a slightly different director spelling
    (see :func:`_director_tokens`) would otherwise be re-searched and dropped
    as unresolved on every run.
    """
    index: dict[str, list[dict]] = {}
    for row in cache_df.itertuples():
        slug = getattr(row, "slug", None)
        if not slug or pd.isna(slug):
            continue
        raw_year = getattr(row, "release_year", None)
        try:
            release_year = int(raw_year) if raw_year is not None else None
        except (TypeError, ValueError):
            release_year = None
        entry = {
            "slug": slug,
            "release_year": release_year,
            "director_tokens": _split_director_tokens(getattr(row, "directors", None), ", "),
        }
        for col in ("title", "french_title", "original_title"):
            norm = _normalize_title(getattr(row, col, None))
            if norm:
                index.setdefault(norm, []).append(entry)
    return index


def _match_cache(film: dict, index: dict[str, list[dict]]) -> str | None:
    """Resolve a film directly against the cache index, confirmed by director overlap.

    Rejects a match with no parseable ``release_year`` or no director tokens on
    either side — the same precision-first rule :func:`_search_letterboxd_slug`
    applies to a live search, needed here too so a recurring French title
    (e.g. ``"Le Retour"``, two unrelated films from 2003 and 2023) can't
    attach to the wrong cache row.
    """
    try:
        year = int(film["release_year"])
    except (TypeError, ValueError):
        return None
    allocine_tokens = _split_director_tokens(film["director"], "|")
    if not allocine_tokens:
        return None

    for title in (film["title"], film["original_title"]):
        norm = _normalize_title(title)
        if not norm:
            continue
        for candidate in index.get(norm, []):
            if candidate["release_year"] == year and _directors_overlap(allocine_tokens, candidate["director_tokens"]):
                return candidate["slug"]
    return None


def enrich_cache_from_showtimes(
    showtimes_path: str | os.PathLike,
    cache_path: str | os.PathLike,
    unresolved_path: str | os.PathLike,
    api_key: str = "",
    *,
    tmdb_groups: frozenset[TmdbColumnGroup] = frozenset(),
) -> None:
    """Expand the Letterboxd metadata cache with films found in a showtimes parquet.

    Reads unique (movie, original_title, director, release_year) tuples from
    ``showtimes_path``. Each tuple is first matched directly against
    ``cache_path`` (title + release_year, confirmed by director token overlap
    — see :func:`_match_cache`); only films that don't already have a cache
    row are resolved via a live Letterboxd search. New slugs get full
    metadata via ``get_letterboxd_data``, stamped with
    ``source="allocine_showtimes"``, and persisted to ``cache_path``; tuples
    that still can't be resolved are written to ``unresolved_path`` for
    visibility.

    Args:
        showtimes_path: Path to the Allocine showtimes parquet.
        cache_path: Path to ``data_letterboxd.parquet`` (read + written in-place).
        unresolved_path: Destination for films that could not be resolved.
        api_key: TMDB API key; forwarded to ``get_letterboxd_data`` to fetch French titles.
    """
    logger.info("Enriching Letterboxd cache from showtimes: %s", showtimes_path)
    showtimes_df = read_parquet_validated(showtimes_path, required_columns=SHOWTIMES.required_columns, label="showtimes")

    # One row per distinct film — not per showtime slot
    key_cols = [c for c in ("movie", "original_title", "director", "release_year") if c in showtimes_df.columns]
    unique_films = showtimes_df[key_cols].drop_duplicates().reset_index(drop=True)
    logger.info("Unique films in showtimes: %d", len(unique_films))

    try:
        # Deliberately unvalidated: this is the "no existing cache, start fresh" path.
        # If schema validation raised here it would be swallowed by the except below and
        # silently rebuild the entire 6,751-film cache from scratch on a transient/partial
        # read — a catastrophic, expensive, silent failure. The cache write further down
        # (write_parquet_validated) is what actually enforces the DATA_LETTERBOXD contract.
        cache_df = pd.read_parquet(cache_path)
        cached_slugs: set[str] = set(cache_df["slug"].dropna().unique())
    except Exception:
        cache_df = pd.DataFrame()
        cached_slugs = set()
    cache_index = _build_cache_index(cache_df)
    logger.debug("cached_slugs: %d preloaded", len(cached_slugs))

    # Lift per-row cleanup out of the loop so the resolver gets a clean list[dict]
    films: list[dict] = []
    for _, row in unique_films.iterrows():
        title = str(row.get("movie") or "").strip()
        if not title:
            continue
        films.append(
            {
                "title": title,
                "original_title": str(row.get("original_title") or "").strip() or None,
                "director": str(row.get("director") or "").strip() or None,
                "release_year": row.get("release_year"),
            }
        )

    # Match against the existing cache before spending a Letterboxd search on it —
    # see _build_cache_index / _match_cache.
    films_to_search: list[dict] = []
    cache_matched = 0
    for film in films:
        slug = _match_cache(film, cache_index)
        if slug:
            cache_matched += 1
            logger.debug("cache match for %r → %s", film["title"], slug)
        else:
            films_to_search.append(film)
    logger.info(
        "%d/%d films matched directly against the cache; resolving the remaining %d against Letterboxd…",
        cache_matched,
        len(films),
        len(films_to_search),
    )

    slugs = asyncio.run(_resolve_all_slugs(films_to_search))
    resolved: list[str] = []
    unresolved: list[dict] = []
    for film, slug in zip(films_to_search, slugs, strict=True):
        if slug and slug not in cached_slugs:
            resolved.append(slug)
            cached_slugs.add(slug)
        elif slug:
            logger.debug("skipped (already cached): %s", slug)
        else:
            unresolved.append(
                {
                    "movie": film["title"],
                    "original_title": film["original_title"],
                    "director": film["director"],
                    "release_year": film["release_year"],
                }
            )

    logger.info("Resolved %d new slugs; %d unresolvable", len(resolved), len(unresolved))

    if resolved:
        cache_df = get_letterboxd_data(resolved, cache_path, api_key, tmdb_groups=tmdb_groups)
        if not cache_df.empty:
            # Stamp the rows this pipeline just added. `resolved` slugs were absent from
            # the cache (filtered against `cached_slugs` above) so they carry no prior
            # source — "allocine_showtimes" is written here and only here, never
            # overwriting a ratings/watchlist provenance set by the user-data pipeline.
            cache_df.loc[cache_df["slug"].isin(set(resolved)), "source"] = "allocine_showtimes"
            write_parquet_validated(
                cache_df, cache_path, required_columns=DATA_LETTERBOXD.required_columns, label="data_letterboxd"
            )

    pd.DataFrame(unresolved).to_parquet(unresolved_path, index=False)
    if unresolved:
        logger.warning("Wrote %d unresolved films to %s", len(unresolved), unresolved_path)
    else:
        logger.info("All films resolved — %s is empty", unresolved_path)
