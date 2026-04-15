import logging

import pandas as pd

logger = logging.getLogger(__name__)


def merge_watchlist_with_letterboxd(watchlist_dict, data_letterboxd_df, watchlist_output_path):
    movies = watchlist_dict.get("data", {})
    logger.info("Watchlist has %d movies.", len(movies))

    rows = [
        {
            "slug": info.get("slug"),
            "name": info.get("name"),
            "release_year": info.get("year"),
        }
        for info in movies.values()
        if info.get("slug")
    ]
    watchlist_df = pd.DataFrame(rows)

    watchlist_df = watchlist_df.merge(data_letterboxd_df, on="slug", how="left", suffixes=("_user", ""))

    # release_year from letterboxd movie data takes precedence; fall back to user data
    if "release_year_user" in watchlist_df.columns:
        watchlist_df["release_year"] = watchlist_df["release_year"].fillna(watchlist_df["release_year_user"])
        watchlist_df.drop(columns=["release_year_user"], inplace=True)

    if "name" in watchlist_df.columns:
        watchlist_df.drop(columns=["name"], inplace=True)

    dup_count = watchlist_df[watchlist_df.duplicated("slug")].shape[0]
    if dup_count > 0:
        logger.error("Watchlist has %d duplicates", dup_count)
        raise ValueError("Watchlist has duplicates, please fix it.")

    if "integration_date" in watchlist_df.columns:
        watchlist_df.drop(columns=["integration_date"], inplace=True)

    column_order = [
        "slug", "title", "release_year", "letterboxd_avg_rating", "genres",
        "description", "tagline", "directors", "runtime",
        "imdb_id", "tmdb_id", "letterboxd_url", "imdb_url", "tmdb_url",
    ]
    existing_cols = [c for c in column_order if c in watchlist_df.columns]
    extra_cols = [c for c in watchlist_df.columns if c not in column_order]
    watchlist_df = watchlist_df[existing_cols + extra_cols]

    logger.info("Saving watchlist data to %s", watchlist_output_path)
    watchlist_df.to_parquet(watchlist_output_path, index=False)

    return watchlist_df
