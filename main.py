import logging
import os
import pathlib
from datetime import datetime

import click
import pandas as pd
from dotenv import load_dotenv
from letterboxdpy.user import User

import letterboxd_data_management.get_letterboxd_data as ldm
import watchlist_management.get_watchlist_infos as wm
import ratings_management.get_ratings_infos as rm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.command()
@click.option('--get_letterboxd', is_flag=True, help='Force a full refresh of the Letterboxd movie cache.')
def movies_management(get_letterboxd):

    current_path = pathlib.Path(__file__).parent.resolve()
    load_dotenv(os.path.join(current_path, '.env'))

    username = os.getenv("LETTERBOXD_USERNAME")
    output_path = os.getenv("OUTPUT_PATH")
    days_to_update = int(os.getenv("LETTERBOXD_DAYS_TO_UPDATE", 365))

    if not username:
        raise ValueError("LETTERBOXD_USERNAME is not set in your .env file.")
    if not output_path:
        raise ValueError("OUTPUT_PATH is not set in your .env file.")

    config = {"days_to_update": days_to_update}

    logger.info("Fetching Letterboxd data for user: %s", username)
    user = User(username)

    films_dict = user.get_films()
    watchlist_dict = user.get_watchlist()

    all_slugs = list(films_dict.get("movies", {}).keys()) + [v["slug"] for v in watchlist_dict.get("data", {}).values() if "slug" in v]
    all_slugs = list(set(all_slugs))
    logger.info("Total unique slugs: %d", len(all_slugs))

    # Letterboxd movie cache
    letterboxd_data_output_path = os.path.join(output_path, 'data_letterboxd.parquet')

    if get_letterboxd:
        logger.info("User requested full refresh of Letterboxd movie cache.")
    data_letterboxd_df = ldm.get_letterboxd_data(all_slugs, letterboxd_data_output_path)

    logger.info("Cache size: %s", data_letterboxd_df.shape)

    # Determine slugs that need refreshing
    slugs_to_refresh = set()

    # 1. Movies older than days_to_update
    if data_letterboxd_df.shape[0] > 0 and "integration_date" in data_letterboxd_df.columns:
        now = pd.to_datetime(datetime.now())
        age_days = (now - data_letterboxd_df["integration_date"]).dt.days
        old_slugs = data_letterboxd_df[age_days > days_to_update]["slug"].tolist()
        if old_slugs:
            logger.info("%d movies need refreshing due to age (>%d days).", len(old_slugs), days_to_update)
            slugs_to_refresh.update(old_slugs)

    if slugs_to_refresh:
        logger.info("Refreshing %d movies.", len(slugs_to_refresh))
        data_letterboxd_df = ldm.refresh_letterboxd_data(
            data_letterboxd_df, list(slugs_to_refresh), letterboxd_data_output_path, config
        )

    # Watchlist output
    watchlist_output_path = os.path.join(output_path, 'watchlist_with_letterboxd.parquet')
    wm.merge_watchlist_with_letterboxd(watchlist_dict, data_letterboxd_df, watchlist_output_path)

    # Ratings output
    ratings_output_path = os.path.join(output_path, 'ratings_with_letterboxd.parquet')
    rm.merge_ratings_with_letterboxd(films_dict, data_letterboxd_df, ratings_output_path)


if __name__ == '__main__':
    movies_management()
