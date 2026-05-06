"""
Movie management system - Letterboxd data aggregation and enrichment.

Orchestrates the complete workflow for fetching user movie data from Letterboxd,
caching movie metadata, and exporting enriched datasets for ratings and watchlists.

This module is the entry point for the application and coordinates:
1. Fetching user data (films, watchlist) from Letterboxd
2. Building and maintaining a cache of movie metadata
3. Refreshing cached data for movies older than configured age
4. Enriching the unified dataset with metadata and splitting for export

Configuration via .env file:
    OUTPUT_PATH: Directory to save parquet output files
    LETTERBOXD_DAYS_TO_UPDATE: Days before movie cache refresh (default: 365)

CLI arguments:
    --username: Letterboxd username to fetch data for
"""

import logging
from datetime import datetime

import click
import pandas as pd
from letterboxdpy.user import User

import modules.get_letterboxd_data as ldm
from modules.config import Settings
from modules.utils import build_movies_df, find_stale_slugs, merge_letterboxd_metadata, save_parquet

# Configure structured logging with timestamps and level indicators
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = Settings()  # type: ignore[call-arg]


@click.command()
@click.option(
    "--username",
    required=True,
    help="Letterboxd username to fetch data for.",
)
@click.option(
    "--reset_database",
    is_flag=True,
    help="Delete the Letterboxd movie cache and rebuild it from scratch.",
)
def movies_management(username: str, reset_database: bool) -> None:  # pragma: no cover
    """
    Main orchestration function for movie data management.

    Fetches Letterboxd user data (films and watchlist), maintains a cache
    of movie metadata, and produces enriched output parquet files.

    Args:
        username: Letterboxd username to fetch data for.
        reset_database: If True, deletes the existing movie metadata cache and rebuilds it from scratch.

    Raises:
        ValueError: If required environment variables (OUTPUT_PATH) are not set.
    """
    output_path = settings.output_path
    days_to_update = settings.letterboxd_days_to_update
    refresh_limit = settings.letterboxd_refresh_limit
    tmdb_api_key = settings.tmdb_api_key
    if not tmdb_api_key:
        logger.warning("TMDB_API_KEY is not set — french_title enrichment will be skipped")

    logger.info("Fetching Letterboxd data for user: %s", username)
    try:
        user = User(username)
    except Exception as e:
        logger.error("Failed to initialize Letterboxd user '%s': %s", username, e)
        raise

    try:
        films_dict = user.get_films()
    except Exception as e:
        logger.error("Failed to fetch films for user '%s': %s", username, e)
        raise

    films_returned = len(films_dict.get("movies", {}))
    films_expected = user.stats.get("films", 0) if isinstance(user.stats, dict) else 0
    if films_expected > 0 and films_returned == 0:
        logger.warning(
            "get_films() returned 0 movies but stats reports %d — likely a scraping issue with the letterboxdpy library.",
            films_expected,
        )

    try:
        watchlist_dict = user.get_watchlist()
    except Exception as e:
        logger.error("Failed to fetch watchlist for user '%s': %s", username, e)
        raise

    if not films_dict.get("movies") or not watchlist_dict.get("data"):
        logger.error("No films or watchlist data returned for user '%s'. Aborting.", username)
        return

    # Build unified DataFrame from both sources before any API calls
    all_movies_df = build_movies_df(films_dict, watchlist_dict)

    dup_slugs = all_movies_df[all_movies_df.duplicated("slug")]["slug"].tolist()
    if dup_slugs:
        raise ValueError(f"Duplicate slugs found across ratings and watchlist: {dup_slugs}")

    logger.info("Total unique slugs: %d", len(all_movies_df))

    # === LETTERBOXD MOVIE CACHE ===
    # Maintain a persistent cache of movie metadata to minimize API calls
    letterboxd_data_output_path = output_path / "data_letterboxd.parquet"

    if reset_database:
        if letterboxd_data_output_path.exists():
            letterboxd_data_output_path.unlink()
            logger.info("Cache file deleted for full rebuild.")
    data_letterboxd_df = ldm.get_letterboxd_data(all_movies_df["slug"].tolist(), letterboxd_data_output_path, tmdb_api_key)

    logger.info("Cache size: %s", data_letterboxd_df.shape)

    # === REFRESH STALE ENTRIES ===
    # Identify movies older than days_to_update threshold for metadata refresh
    slugs_to_refresh = set()

    # Flag movies that exceed age threshold for refresh
    if data_letterboxd_df.shape[0] > 0 and "integration_date" in data_letterboxd_df.columns:
        now = pd.to_datetime(datetime.now())
        stale = find_stale_slugs(data_letterboxd_df, days_to_update, now)
        if stale:
            total_stale = len(stale)
            if refresh_limit is not None:
                stale = stale[:refresh_limit]
            logger.info(
                "%d/%d stale movies will be refreshed (limit: %s, threshold: >%d days).",
                len(stale),
                total_stale,
                refresh_limit or "none",
                days_to_update,
            )
            slugs_to_refresh.update(stale)

    # Refresh outdated entries with fresh metadata
    if slugs_to_refresh:
        logger.info("Refreshing %d movies.", len(slugs_to_refresh))
        data_letterboxd_df = ldm.refresh_letterboxd_data(
            data_letterboxd_df,
            list(slugs_to_refresh),
            letterboxd_data_output_path,
            tmdb_api_key,
        )

    # === ENRICH AND EXPORT ===
    all_movies_df = merge_letterboxd_metadata(all_movies_df, data_letterboxd_df)

    ratings_column_order = [
        "slug",
        "user_rating",
        "liked",
        "title",
        "french_title",
        "release_year",
        "letterboxd_avg_rating",
        "genres",
        "description",
        "tagline",
        "directors",
        "runtime",
        "imdb_id",
        "tmdb_id",
        "letterboxd_url",
        "imdb_url",
        "tmdb_url",
    ]
    watchlist_column_order = [
        "slug",
        "title",
        "french_title",
        "release_year",
        "letterboxd_avg_rating",
        "genres",
        "description",
        "tagline",
        "directors",
        "runtime",
        "imdb_id",
        "tmdb_id",
        "letterboxd_url",
        "imdb_url",
        "tmdb_url",
    ]

    ratings_df = all_movies_df[all_movies_df["source"] == "ratings"].drop(columns=["source"])
    watchlist_df = all_movies_df[all_movies_df["source"] == "watchlist"].drop(columns=["source"])

    ratings_output_path = output_path / "ratings_with_letterboxd.parquet"
    logger.info("Saving ratings data to %s", ratings_output_path)
    save_parquet(ratings_df, ratings_column_order, ratings_output_path)

    watchlist_output_path = output_path / "watchlist_with_letterboxd.parquet"
    logger.info("Saving watchlist data to %s", watchlist_output_path)
    save_parquet(watchlist_df, watchlist_column_order, watchlist_output_path)


if __name__ == "__main__":
    movies_management()
