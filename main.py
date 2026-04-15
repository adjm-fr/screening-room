"""
Movie management system - Letterboxd data aggregation and enrichment.

Orchestrates the complete workflow for fetching user movie data from Letterboxd,
caching movie metadata, and exporting enriched datasets for ratings and watchlists.

This module is the entry point for the application and coordinates:
1. Fetching user data (films, watchlist) from Letterboxd
2. Building and maintaining a cache of movie metadata
3. Refreshing cached data for movies older than configured age
4. Merging user data with enriched metadata for export

Configuration via .env file:
    LETTERBOXD_USERNAME: Letterboxd username to fetch data for
    OUTPUT_PATH: Directory to save parquet output files
    LETTERBOXD_DAYS_TO_UPDATE: Days before movie cache refresh (default: 365)
"""

import logging
import os
import pathlib
from datetime import datetime

import click
import pandas as pd
from dotenv import load_dotenv
from letterboxdpy.user import User

import letterboxd_data_management.get_letterboxd_data as ldm
import ratings_management.get_ratings_infos as rm
import watchlist_management.get_watchlist_infos as wm

# Configure structured logging with timestamps and level indicators
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--get_letterboxd",
    is_flag=True,
    help="Force a full refresh of the Letterboxd movie cache.",
)
def movies_management(get_letterboxd: bool) -> None:
    """
    Main orchestration function for movie data management.

    Fetches Letterboxd user data (films and watchlist), maintains a cache
    of movie metadata, and produces enriched output parquet files.

    Args:
        get_letterboxd: If True, forces a full refresh of the movie metadata cache.

    Raises:
        ValueError: If required environment variables (LETTERBOXD_USERNAME,
                    OUTPUT_PATH) are not set.
    """
    # Load environment configuration
    current_path = pathlib.Path(__file__).parent.resolve()
    load_dotenv(os.path.join(current_path, ".env"))

    # Retrieve required configuration
    username = os.getenv("LETTERBOXD_USERNAME")
    output_path = os.getenv("OUTPUT_PATH")
    days_to_update = int(os.getenv("LETTERBOXD_DAYS_TO_UPDATE", 365))
    refresh_limit_raw = os.getenv("LETTERBOXD_REFRESH_LIMIT")
    refresh_limit = int(refresh_limit_raw) if refresh_limit_raw else None

    # Validate required config
    if not username:
        raise ValueError("LETTERBOXD_USERNAME is not set in your .env file.")
    if not output_path:
        raise ValueError("OUTPUT_PATH is not set in your .env file.")

    config = {"days_to_update": days_to_update}

    logger.info("Fetching Letterboxd data for user: %s", username)
    # Fetch user's film ratings and watchlist from Letterboxd
    user = User(username)

    films_dict = user.get_films()
    watchlist_dict = user.get_watchlist()

    # Consolidate all unique movie slugs from both films and watchlist
    all_slugs = list(films_dict.get("movies", {}).keys()) + [
        v["slug"]
        for v in watchlist_dict.get("data", {}).values()
        if "slug" in v
    ]
    all_slugs = list(set(all_slugs))  # Remove duplicates
    logger.info("Total unique slugs: %d", len(all_slugs))

    # === LETTERBOXD MOVIE CACHE ===
    # Maintain a persistent cache of movie metadata to minimize API calls
    letterboxd_data_output_path = os.path.join(output_path, "data_letterboxd.parquet")

    if get_letterboxd:
        logger.info("User requested full refresh of Letterboxd movie cache.")
    # Fetch new movies and merge with existing cache
    data_letterboxd_df = ldm.get_letterboxd_data(all_slugs, letterboxd_data_output_path)

    logger.info("Cache size: %s", data_letterboxd_df.shape)

    # === REFRESH STALE ENTRIES ===
    # Identify movies older than days_to_update threshold for metadata refresh
    slugs_to_refresh = set()

    # Flag movies that exceed age threshold for refresh
    if (
        data_letterboxd_df.shape[0] > 0
        and "integration_date" in data_letterboxd_df.columns
    ):
        now = pd.to_datetime(datetime.now())
        age_days = (now - data_letterboxd_df["integration_date"]).dt.days
        old_slugs = data_letterboxd_df[age_days > days_to_update]["slug"].tolist()
        if old_slugs:
            total_stale = len(old_slugs)
            if refresh_limit is not None:
                old_slugs = old_slugs[:refresh_limit]
            logger.info(
                "%d/%d stale movies will be refreshed (limit: %s, threshold: >%d days).",
                len(old_slugs),
                total_stale,
                refresh_limit or "none",
                days_to_update,
            )
            slugs_to_refresh.update(old_slugs)

    # Refresh outdated entries with fresh metadata
    if slugs_to_refresh:
        logger.info("Refreshing %d movies.", len(slugs_to_refresh))
        data_letterboxd_df = ldm.refresh_letterboxd_data(
            data_letterboxd_df,
            list(slugs_to_refresh),
            letterboxd_data_output_path,
            config,
        )

    # === EXPORT ENRICHED DATASETS ===
    # Merge user data with movie metadata for downstream analysis
    watchlist_output_path = os.path.join(
        output_path, "watchlist_with_letterboxd.parquet"
    )
    wm.merge_watchlist_with_letterboxd(
        watchlist_dict, data_letterboxd_df, watchlist_output_path
    )

    ratings_output_path = os.path.join(
        output_path, "ratings_with_letterboxd.parquet"
    )
    rm.merge_ratings_with_letterboxd(
        films_dict, data_letterboxd_df, ratings_output_path
    )


if __name__ == "__main__":
    movies_management()
