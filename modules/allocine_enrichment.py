"""
Allocine → Letterboxd cache enrichment.

Resolves Allocine film tuples (title, original_title, director, release_year) to
Letterboxd slugs and expands data_letterboxd.parquet to cover every film in a
showtimes parquet, not only the user's watchlist and ratings.
"""

import asyncio
import logging
import os

import pandas as pd
from letterboxdpy.search import Search, SearchFilter
from tenacity import retry, stop_after_attempt, wait_exponential

from modules.get_letterboxd_data import get_letterboxd_data

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def _search_films(query: str) -> list[dict]:
    """Run a Letterboxd film search, retrying on transient failures."""
    return Search(query, SearchFilter.FILMS).results.get("results", [])


def _allocine_director_names(value: str | None) -> set[str]:
    """Split an Allocine director string (pipe-separated) into a set of lowercased names."""
    if not value:
        return set()
    return {name.strip().lower() for name in value.split("|") if name.strip()}


def _letterboxd_director_names(item: dict) -> set[str]:
    """Extract lowercased director names from a Letterboxd search result."""
    return {(d.get("name") or "").strip().lower() for d in item.get("directors") or [] if d.get("name")}


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
    allocine_directors = _allocine_director_names(director)

    for item in results:
        item_year = str(item.get("year", ""))
        if item_year != year_str:
            logger.debug("    skip slug=%s: year %s ≠ %s", item.get("slug"), item_year, year_str)
            continue
        lb_directors = _letterboxd_director_names(item)
        if allocine_directors & lb_directors:
            slug = item.get("slug") or None
            logger.debug("    match slug=%s (directors %s ∩ %s)", slug, allocine_directors, lb_directors)
            return slug
        logger.debug("    skip slug=%s: directors %s ∩ %s = ∅", item.get("slug"), allocine_directors, lb_directors)

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


def enrich_cache_from_showtimes(
    showtimes_path: str | os.PathLike,
    cache_path: str | os.PathLike,
    unresolved_path: str | os.PathLike,
    api_key: str = "",
) -> None:
    """Expand the Letterboxd metadata cache with films found in a showtimes parquet.

    Reads unique (movie, original_title, director, release_year) tuples from
    ``showtimes_path``, resolves each to a Letterboxd slug (skipping slugs already
    in ``cache_path``), fetches full metadata for new slugs via ``get_letterboxd_data``,
    stamps those new rows with ``source="allocine_showtimes"``, persists the cache to
    ``cache_path``, and writes unresolvable tuples to ``unresolved_path`` for visibility.

    Args:
        showtimes_path: Path to the Allocine showtimes parquet.
        cache_path: Path to ``data_letterboxd.parquet`` (read + written in-place).
        unresolved_path: Destination for films that could not be resolved.
        api_key: TMDB API key; forwarded to ``get_letterboxd_data`` to fetch French titles.
    """
    logger.info("Enriching Letterboxd cache from showtimes: %s", showtimes_path)
    showtimes_df = pd.read_parquet(showtimes_path)

    # One row per distinct film — not per showtime slot
    key_cols = [c for c in ("movie", "original_title", "director", "release_year") if c in showtimes_df.columns]
    unique_films = showtimes_df[key_cols].drop_duplicates().reset_index(drop=True)
    logger.info("Unique films in showtimes: %d", len(unique_films))

    try:
        cache_df = pd.read_parquet(cache_path)
        cached_slugs: set[str] = set(cache_df["slug"].dropna().unique())
    except Exception:
        cached_slugs = set()
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

    logger.info("Resolving %d unique films against Letterboxd…", len(films))
    slugs = asyncio.run(_resolve_all_slugs(films))
    resolved: list[str] = []
    unresolved: list[dict] = []
    for film, slug in zip(films, slugs, strict=True):
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
        cache_df = get_letterboxd_data(resolved, cache_path, api_key)
        if not cache_df.empty:
            # Stamp the rows this pipeline just added. `resolved` slugs were absent from
            # the cache (filtered against `cached_slugs` above) so they carry no prior
            # source — "allocine_showtimes" is written here and only here, never
            # overwriting a ratings/watchlist provenance set by the user-data pipeline.
            cache_df.loc[cache_df["slug"].isin(set(resolved)), "source"] = "allocine_showtimes"
            cache_df.to_parquet(cache_path, index=False)

    pd.DataFrame(unresolved).to_parquet(unresolved_path, index=False)
    if unresolved:
        logger.warning("Wrote %d unresolved films to %s", len(unresolved), unresolved_path)
    else:
        logger.info("All films resolved — %s is empty", unresolved_path)
