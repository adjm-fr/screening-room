"""
Movie data retrieval and caching from Letterboxd API.

This module handles fetching movie metadata from Letterboxd using the letterboxdpy library,
with efficient caching and parallel request processing.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from letterboxdpy.movie import Movie

logger = logging.getLogger(__name__)


def _fetch_movie(slug: str) -> dict | None:
    """
    Fetch movie metadata from Letterboxd for a single movie slug.

    Args:
        slug: The Letterboxd movie slug identifier.

    Returns:
        Dictionary containing movie metadata (title, year, genres, ratings, etc.).
        Returns None if fetching fails.

    Note:
        Directors and genres are extracted from the crew and genres fields,
        which may have inconsistent structures (sometimes dicts, sometimes strings).
    """
    try:
        movie = Movie(slug)
        # Extract directors from crew object if available
        directors = []
        if movie.crew and "director" in movie.crew:
            directors = [p["name"] for p in movie.crew["director"]]

        # Normalize genre data (may be dict with "name" key or plain string)
        genres = []
        if movie.genres:
            genres = [g["name"] if isinstance(g, dict) else g for g in movie.genres]

        return {
            "slug": slug,
            "title": movie.title,
            "release_year": movie.year,
            "runtime": getattr(movie, "runtime", None),
            "genres": ", ".join(genres) if genres else None,
            "description": getattr(movie, "description", None),
            "tagline": getattr(movie, "tagline", None),
            "letterboxd_avg_rating": movie.rating,
            "directors": ", ".join(directors) if directors else None,
            "imdb_id": movie.imdb_id,
            "tmdb_id": movie.tmdb_id,
            "letterboxd_url": movie.url,
            "imdb_url": getattr(movie, "imdb_link", None),
            "tmdb_url": getattr(movie, "tmdb_link", None),
        }
    except Exception as e:
        logger.error("Failed to fetch Movie data for slug '%s': %s", slug, e)
        return None


def get_letterboxd_data(all_slugs: list[str], output_path: str) -> pd.DataFrame:
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
    cached_slugs = set(data_df["slug"].unique()) if data_df.shape[0] > 0 else set()
    new_slugs = [s for s in all_slugs if s not in cached_slugs]

    logger.info("New slugs to fetch: %d", len(new_slugs))

    if new_slugs:
        results = []
        # Parallel fetch with thread pool (API I/O bound)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_movie, slug): slug for slug in new_slugs}
            logger.info("Submitted %d fetch jobs to executor", len(futures))
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    logger.error("Unhandled exception for slug '%s': %s", slug, e)
                    result = None
                if result:
                    results.append(result)
                logger.info("Fetched %d/%d (%s)", len(results), len(new_slugs), slug)

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
    data_df: pd.DataFrame, slugs_to_refresh: list[str], output_path: str, config: dict
) -> pd.DataFrame:
    """
    Update existing cached movie data for specified slugs.

    Refetches data for movies that have aged beyond the configured days_to_update
    threshold. Updates the cache with fresh metadata while preserving other entries.

    Args:
        data_df: Existing DataFrame with cached movie data.
        slugs_to_refresh: List of movie slugs to update.
        output_path: Path to cache file (parquet format).
        config: Configuration dict containing 'days_to_update' key (currently unused but
                passed for future extensibility).

    Returns:
        Updated DataFrame with refreshed movie data and new integration_date.
    """
    if not slugs_to_refresh:
        logger.info("No movies to refresh")
        return data_df

    logger.info("Refreshing %d movies", len(slugs_to_refresh))
    results = []
    # Parallel refresh with thread pool
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_movie, slug): slug for slug in slugs_to_refresh}
        logger.info("Submitted %d refresh jobs to executor", len(futures))
        for future in as_completed(futures):
            slug = futures[future]
            try:
                result = future.result()
            except Exception as e:
                logger.error("Unhandled exception for slug '%s': %s", slug, e)
                result = None
            if result:
                results.append(result)
            logger.info("Refreshed %d/%d (%s)", len(results), len(slugs_to_refresh), slug)

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
