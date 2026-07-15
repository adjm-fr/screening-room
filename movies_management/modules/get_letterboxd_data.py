"""
Movie data retrieval and caching from Letterboxd API.

This module handles fetching movie metadata from Letterboxd using the letterboxdpy library,
with efficient caching and parallel request processing.
"""

import asyncio
import logging
import os
from datetime import datetime

import httpx
import pandas as pd
from letterboxdpy.movie import Movie
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TMDB_API_URL = "https://api.themoviedb.org/3"

# Shared retry policy for transient API failures: 3 attempts with exponential backoff,
# re-raising the final error so callers can degrade gracefully (return None / skip movie).
_RETRY_STOP = stop_after_attempt(3)
_RETRY_WAIT = wait_exponential(multiplier=1, max=10)


@retry(
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
async def _get_tmdb_movie(client: httpx.AsyncClient, tmdb_id: str, api_key: str) -> httpx.Response:
    """GET a TMDB movie, retrying on transport errors and 429/5xx responses."""
    resp = await client.get(
        f"{TMDB_API_URL}/movie/{tmdb_id}",
        params={"language": "fr-FR", "api_key": api_key},
        timeout=10,
    )
    # Treat rate-limit / server errors as transient so tenacity retries them;
    # 4xx (other than 429) raise too but won't be retried (not in retry_if_exception_type
    # scope below) — caught by _fetch_french_title and surfaced as None.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


async def _fetch_french_title(client: httpx.AsyncClient, tmdb_id: str | None, api_key: str | None) -> str | None:
    """Fetch a film's French title from TMDB using an injected async client.

    Returns None when ``tmdb_id`` or ``api_key`` is falsy, on any non-200 response, or
    when the request keeps failing after retries — never raises into the batch.
    """
    if not tmdb_id or not api_key:
        return None
    try:
        resp = await _get_tmdb_movie(client, tmdb_id, api_key)
        if resp.status_code == 200:
            return resp.json().get("title")
        logger.debug("TMDB returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except Exception as e:
        logger.debug("TMDB fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return None


@retry(
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
async def _get_tmdb_credits(client: httpx.AsyncClient, tmdb_id: str, api_key: str) -> httpx.Response:
    """GET a TMDB movie's credits, retrying on transport errors and 429/5xx responses."""
    resp = await client.get(
        f"{TMDB_API_URL}/movie/{tmdb_id}/credits",
        params={"api_key": api_key},
        timeout=10,
    )
    # Same contract as _get_tmdb_movie: 429/5xx are retried, other 4xx raise but aren't
    # retried — caught by _fetch_cast and surfaced as None.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


async def _fetch_cast(client: httpx.AsyncClient, tmdb_id: str | None, api_key: str | None) -> str | None:
    """Fetch a film's top-8 billed cast from TMDB using an injected async client.

    TMDB returns ``cast`` pre-sorted by billing ``order``, so the first 8 entries are the
    leads — kept intentionally short to keep the taste signal clean.

    Returns None when ``tmdb_id`` or ``api_key`` is falsy, on any non-200 response, or
    when the request keeps failing after retries — never raises into the batch.
    """
    if not tmdb_id or not api_key:
        return None
    try:
        resp = await _get_tmdb_credits(client, tmdb_id, api_key)
        if resp.status_code == 200:
            cast = resp.json().get("cast") or []
            names = [member["name"] for member in cast[:8] if member.get("name")]
            return ", ".join(names) or None
        logger.debug("TMDB credits returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except Exception as e:
        logger.debug("TMDB credits fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return None


@retry(
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
async def _get_tmdb_videos(client: httpx.AsyncClient, tmdb_id: str, api_key: str) -> httpx.Response:
    """GET a TMDB movie's videos, retrying on transport errors and 429/5xx responses."""
    resp = await client.get(
        f"{TMDB_API_URL}/movie/{tmdb_id}/videos",
        params={"include_video_language": "fr,en,null", "api_key": api_key},
        timeout=10,
    )
    # Same contract as _get_tmdb_movie: 429/5xx are retried, other 4xx raise but aren't
    # retried — caught by _fetch_trailer and surfaced as None.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


# Lower is better: French trailers are preferred over English, which are preferred over
# anything else. Anything not in this mapping (including missing iso_639_1) sorts last.
_TRAILER_LANGUAGE_PRIORITY = {"fr": 0, "en": 1}


async def _fetch_trailer(client: httpx.AsyncClient, tmdb_id: str | None, api_key: str | None) -> str | None:
    """Fetch a film's official YouTube trailer link from TMDB using an injected async client.

    Filters TMDB's videos to ``site == "YouTube"``, ``type == "Trailer"``, ``official is
    True``, then picks the best-language match: French, else English, else any other
    language (see ``_TRAILER_LANGUAGE_PRIORITY``).

    Returns None when ``tmdb_id`` or ``api_key`` is falsy, when no video matches the
    filters, on any non-200 response, or when the request keeps failing after retries —
    never raises into the batch.
    """
    if not tmdb_id or not api_key:
        return None
    try:
        resp = await _get_tmdb_videos(client, tmdb_id, api_key)
        if resp.status_code == 200:
            videos = resp.json().get("results") or []
            trailers = [
                v for v in videos if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("official") is True
            ]
            if not trailers:
                return None
            best = min(trailers, key=lambda v: _TRAILER_LANGUAGE_PRIORITY.get(v.get("iso_639_1"), 2))
            key = best.get("key")
            return f"https://www.youtube.com/watch?v={key}" if key else None
        logger.debug("TMDB videos returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except Exception as e:
        logger.debug("TMDB videos fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return None


@retry(stop=_RETRY_STOP, wait=_RETRY_WAIT, reraise=True)
def _build_movie(slug: str) -> Movie:
    """Construct a letterboxdpy ``Movie`` (the blocking scrape), retrying on transient errors."""
    return Movie(slug)


def _fetch_movie(slug: str) -> dict | None:
    """
    Fetch movie metadata from Letterboxd for a single movie slug.

    Args:
        slug: The Letterboxd movie slug identifier.

    Returns:
        Dictionary containing movie metadata (title, year, genres, ratings, etc.) with
        ``french_title``, ``cast``, and ``trailer_url`` left as None — they are filled in
        by ``_fetch_all`` via TMDB.
        Returns None if fetching fails.

    Note:
        - Letterboxd's own cast, trailer, and popular_reviews fields are excluded from this
          output; ``cast`` (top-8 billed) and ``trailer_url`` are sourced from TMDB instead,
          mirroring how ``french_title`` is added — see ``_fetch_cast`` / ``_fetch_trailer``.
        - genres is split into genres/themes/mini_themes based on the "type" field.
        - details are expanded into one key per type (e.g. "studio", "country", "language").
        - crew is filtered to director(s), producer(s), and writer(s) only.
    """
    try:
        movie = _build_movie(slug)

        # --- Genres / themes / mini-themes ---
        # movie.genres is a list[dict] with keys: type, name, slug, url
        # The "type" field comes from the Letterboxd URL path segment (genre, theme, mini-theme)
        raw_genres = movie.genres or []
        genres = ", ".join(g["name"] for g in raw_genres if g.get("type") == "genre") or None
        themes = ", ".join(g["name"] for g in raw_genres if g.get("type") == "theme") or None
        mini_themes = ", ".join(g["name"] for g in raw_genres if g.get("type") == "mini-theme") or None

        # --- Details (studio, country, language, …) ---
        # movie.details is a list[dict] with keys: type, name, slug, url
        # Group by type and comma-join names; each type becomes its own column.
        details_grouped: dict[str, list[str]] = {}
        for d in movie.details or []:
            t = d.get("type")
            if t:
                details_grouped.setdefault(t, []).append(d["name"])
        details_by_type = {t: ", ".join(names) for t, names in details_grouped.items()}

        # --- Crew (director, producer, writer only) ---
        crew = movie.crew or {}
        directors = ", ".join(p["name"] for p in crew.get("director", [])) or None
        producers = ", ".join(p["name"] for p in crew.get("producer", [])) or None
        writers = ", ".join(p["name"] for p in crew.get("writer", [])) or None

        return {
            # Identifiers
            "slug": slug,
            "movie_id": movie.id,
            "letterboxd_url": movie.url,
            "imdb_id": movie.imdb_id,
            "tmdb_id": movie.tmdb_id,
            "imdb_url": movie.imdb_link,
            "tmdb_url": movie.tmdb_link,
            # Core info
            "title": movie.title,
            "french_title": None,  # filled in by _fetch_all via TMDB
            "cast": None,  # filled in by _fetch_all via TMDB (top-8 billed)
            "trailer_url": None,  # filled in by _fetch_all via TMDB
            "original_title": movie.original_title,
            "release_year": movie.year,
            "runtime": movie.runtime,
            "tagline": movie.tagline,
            "description": movie.description,
            "letterboxd_avg_rating": movie.rating,
            # Media
            "poster_url": movie.poster,
            "banner_url": movie.banner,
            # Genres / themes
            "genres": genres,
            "themes": themes,
            "mini_themes": mini_themes,
            # Crew
            "directors": directors,
            "producers": producers,
            "writers": writers,
            # Details — dynamic keys per type (e.g. studio, country, language)
            **details_by_type,
        }
    except Exception as e:
        logger.error("Failed to fetch Movie data for slug '%s': %s", slug, e)
        return None


async def _fetch_all(slugs: list[str], api_key: str = "", concurrency: int = 20) -> list[dict | None]:
    """Run _fetch_movie for every slug concurrently, then attach TMDB enrichment.

    A single shared ``httpx.AsyncClient`` is opened for the whole batch so all TMDB
    lookups reuse pooled connections; the blocking Letterboxd scrape still runs in a
    worker thread per slug. The three TMDB lookups (french_title, cast, trailer_url)
    for a given movie run concurrently in a nested ``asyncio.TaskGroup``.
    """
    sem = asyncio.Semaphore(concurrency)
    total = len(slugs)
    done = 0
    results: list[dict | None] = [None] * total

    async def _guarded(client: httpx.AsyncClient, i: int, slug: str) -> None:
        nonlocal done
        async with sem:
            result = await asyncio.to_thread(_fetch_movie, slug)
            if result is not None:
                tmdb_id = result.get("tmdb_id")
                async with asyncio.TaskGroup() as movie_tg:
                    french_title = movie_tg.create_task(_fetch_french_title(client, tmdb_id, api_key))
                    cast = movie_tg.create_task(_fetch_cast(client, tmdb_id, api_key))
                    trailer_url = movie_tg.create_task(_fetch_trailer(client, tmdb_id, api_key))
                result["french_title"] = french_title.result()
                result["cast"] = cast.result()
                result["trailer_url"] = trailer_url.result()
            results[i] = result
        done += 1
        if done % 50 == 0 or done == total:
            logger.info("Fetched %d/%d", done, total)

    async with httpx.AsyncClient() as client, asyncio.TaskGroup() as tg:
        for i, slug in enumerate(slugs):
            tg.create_task(_guarded(client, i, slug))

    return results


def get_letterboxd_data(all_slugs: list[str], output_path: str | os.PathLike, api_key: str = "") -> pd.DataFrame:
    """
    Fetch Letterboxd movie data, reusing an on-disk cache to skip already-known slugs.

    Loads the existing cache from ``output_path`` (read-only) and fetches only
    new/missing movies using parallel requests. **Does not persist** — the caller
    owns the single cache write so it can assign provenance (``source``) first.

    Args:
        all_slugs: List of Letterboxd movie slugs to fetch data for.
        output_path: Path to the existing cache file, read to skip cached slugs.
        api_key: TMDB API key for authenticated requests.

    Returns:
        DataFrame combining the loaded cache and any newly fetched rows, with columns:
        slug, title, release_year, runtime, genres, description, tagline,
        letterboxd_avg_rating, directors, imdb_id, tmdb_id, letterboxd_url, imdb_url,
        tmdb_url, integration_date. The caller persists it (and assigns ``source``).
    """
    # Load existing cache to avoid refetching
    try:
        data_df = pd.read_parquet(output_path)
        logger.info("Loaded existing cache: %d movies", data_df.shape[0])
    except Exception:
        logger.info("No existing cache found — starting fresh")
        data_df = pd.DataFrame()

    # Identify slugs that need fetching
    cached_slugs = set(data_df["slug"].unique()) if not data_df.empty else set()
    new_slugs = [s for s in all_slugs if s not in cached_slugs]

    logger.info("New slugs to fetch: %d", len(new_slugs))

    if new_slugs:
        fetched = asyncio.run(_fetch_all(new_slugs, api_key))
        results = [r for r in fetched if r]  # Filter out None results from failed fetches

        if results:
            new_df = pd.DataFrame(results)
            # Mark when data was integrated into cache for refresh tracking
            now = pd.to_datetime(datetime.now().date())
            new_df["integration_date"] = now
            data_df = pd.concat([data_df, new_df], ignore_index=True)
            logger.info("Fetched %d new movies (caller persists)", len(results))
    else:
        logger.info("No new slugs to fetch")

    return data_df


def refresh_letterboxd_data(data_df: pd.DataFrame, slugs_to_refresh: list[str], api_key: str = "") -> pd.DataFrame:
    """
    Refetch metadata for the given slugs and return the updated DataFrame.

    Refetches movies that have aged beyond the configured days_to_update threshold,
    updating them in-place while preserving other entries (and their ``source``).
    **Does not persist** — the caller owns the single cache write.

    Args:
        data_df: Existing DataFrame with cached movie data.
        slugs_to_refresh: List of movie slugs to update.
        api_key: TMDB API key for authenticated requests.

    Returns:
        Updated DataFrame with refreshed movie data and new integration_date.
        The caller persists it.
    """
    if not slugs_to_refresh:
        logger.info("No movies to refresh")
        return data_df

    logger.info("Refreshing %d movies", len(slugs_to_refresh))

    fetched = asyncio.run(_fetch_all(slugs_to_refresh, api_key))
    results = [r for r in fetched if r]

    fetched_slugs = {r["slug"] for r in results}
    dead_slugs = [s for s in slugs_to_refresh if s not in fetched_slugs]
    if dead_slugs:
        logger.info("Removing %d stale slug(s) no longer on Letterboxd: %s", len(dead_slugs), dead_slugs)
        data_df = data_df[~data_df["slug"].isin(dead_slugs)]

    if results:
        now = pd.to_datetime(datetime.now().date())
        refresh_df = pd.DataFrame(results)
        refresh_df["integration_date"] = now

        # DataFrame.update() silently ignores columns absent from the target, so refreshing
        # a cache built before a column existed (e.g. cast/trailer_url, added after earlier
        # rows were cached) would otherwise drop that column's refreshed values instead of
        # populating them. Pre-create any such columns (as null) so update() can fill them.
        for col in refresh_df.columns.difference(data_df.columns):
            data_df[col] = None

        # Update cache: merge refreshed data with existing, keyed by slug
        data_df = data_df.set_index("slug")
        data_df.update(refresh_df.set_index("slug"))
        data_df = data_df.reset_index()
        logger.info("Refreshed %d movies in cache", len(results))

    return data_df
