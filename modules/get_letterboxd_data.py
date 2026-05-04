"""
Movie data retrieval and caching from Letterboxd API.

This module handles fetching movie metadata from Letterboxd using the letterboxdpy library,
with efficient caching and parallel request processing.
"""

import asyncio
import logging
import os
from datetime import datetime

import pandas as pd
import requests
from letterboxdpy.movie import Movie

logger = logging.getLogger(__name__)

TMDB_API_URL = "https://api.themoviedb.org/3"


def _fetch_french_title(tmdb_id: str | None, api_key: str | None) -> str | None:
    if not tmdb_id or not api_key:
        return None
    try:
        resp = requests.get(
            f"{TMDB_API_URL}/movie/{tmdb_id}",
            params={"language": "fr-FR", "api_key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("title")
        logger.debug("TMDB returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except Exception as e:
        logger.debug("TMDB fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return None


def _fetch_movie(slug: str, api_key: str | None = None) -> dict | None:
    """
    Fetch movie metadata from Letterboxd for a single movie slug.

    Args:
        slug: The Letterboxd movie slug identifier.

    Returns:
        Dictionary containing movie metadata (title, year, genres, ratings, etc.).
        Returns None if fetching fails.

    Note:
        - cast, trailer, and popular_reviews are excluded from the output.
        - genres is split into genres/themes/mini_themes based on the "type" field.
        - details are expanded into one key per type (e.g. "studio", "country", "language").
        - crew is filtered to director(s), producer(s), and writer(s) only.
    """
    try:
        movie = Movie(slug)

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

        french_title = _fetch_french_title(movie.tmdb_id, api_key)

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
            "french_title": french_title,
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
    """Run _fetch_movie for every slug concurrently, capped at _CONCURRENCY threads."""
    sem = asyncio.Semaphore(concurrency)
    total = len(slugs)
    done = 0
    results: list[dict | None] = [None] * total

    async def _guarded(i: int, slug: str) -> None:
        nonlocal done
        async with sem:
            results[i] = await asyncio.to_thread(_fetch_movie, slug, api_key)
        done += 1
        if done % 50 == 0 or done == total:
            logger.info("Fetched %d/%d", done, total)

    async with asyncio.TaskGroup() as tg:
        for i, slug in enumerate(slugs):
            tg.create_task(_guarded(i, slug))

    return results


def get_letterboxd_data(all_slugs: list[str], output_path: str | os.PathLike, api_key: str = "") -> pd.DataFrame:
    """
    Fetch and cache Letterboxd movie data.

    Loads existing cache from parquet file and fetches only new/missing movies
    using parallel requests. Caches results to avoid redundant API calls.

    Args:
        all_slugs: List of Letterboxd movie slugs to fetch data for.
        output_path: Path to cache file (parquet format).

    Returns:
        DataFrame containing all movie metadata with columns: slug, title, release_year,
        runtime, genres, description, tagline, letterboxd_avg_rating, directors,
        imdb_id, tmdb_id, letterboxd_url, imdb_url, tmdb_url, integration_date.
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
            data_df.to_parquet(output_path, index=False)
            logger.info("Added %d new movies to cache", len(results))
    else:
        logger.info("No new slugs to fetch")

    return data_df


def refresh_letterboxd_data(
    data_df: pd.DataFrame, slugs_to_refresh: list[str], output_path: str | os.PathLike, api_key: str = ""
) -> pd.DataFrame:
    """
    Update existing cached movie data for specified slugs.

    Refetches data for movies that have aged beyond the configured days_to_update
    threshold. Updates the cache with fresh metadata while preserving other entries.

    Args:
        data_df: Existing DataFrame with cached movie data.
        slugs_to_refresh: List of movie slugs to update.
        output_path: Path to cache file (parquet format).
        api_key: TMDB API key for authenticated requests.

    Returns:
        Updated DataFrame with refreshed movie data and new integration_date.
    """
    if not slugs_to_refresh:
        logger.info("No movies to refresh")
        return data_df

    logger.info("Refreshing %d movies", len(slugs_to_refresh))

    fetched = asyncio.run(_fetch_all(slugs_to_refresh, api_key))
    results = [r for r in fetched if r]

    if results:
        now = pd.to_datetime(datetime.now().date())
        refresh_df = pd.DataFrame(results)
        refresh_df["integration_date"] = now

        # Update cache: merge refreshed data with existing, keyed by slug
        data_df = data_df.set_index("slug")
        data_df.update(refresh_df.set_index("slug"))
        data_df = data_df.reset_index()
        data_df.to_parquet(output_path, index=False)
        logger.info("Refreshed %d movies in cache", len(results))

    return data_df
