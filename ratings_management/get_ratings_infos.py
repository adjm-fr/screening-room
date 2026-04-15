import logging

import pandas as pd

logger = logging.getLogger(__name__)


def merge_ratings_with_letterboxd(films_dict, data_letterboxd_df, ratings_output_path):
    movies = films_dict.get("movies", {})
    logger.info("Ratings has %d movies.", len(movies))

    rows = [
        {
            "slug": slug,
            "user_rating": info.get("rating"),
            "name": info.get("name"),
            "release_year": info.get("year"),
            "liked": info.get("liked"),
        }
        for slug, info in movies.items()
    ]
    ratings_df = pd.DataFrame(rows)

    ratings_df = ratings_df.merge(data_letterboxd_df, on="slug", how="left", suffixes=("_user", ""))

    # release_year from letterboxd movie data takes precedence; fall back to user data
    if "release_year_user" in ratings_df.columns:
        ratings_df["release_year"] = ratings_df["release_year"].fillna(ratings_df["release_year_user"])
        ratings_df.drop(columns=["release_year_user"], inplace=True)

    # Drop name from user data (title from Movie object is more complete)
    if "name" in ratings_df.columns:
        ratings_df.drop(columns=["name"], inplace=True)

    dup_count = ratings_df[ratings_df.duplicated("slug")].shape[0]
    if dup_count > 0:
        logger.error("Ratings has %d duplicates", dup_count)
        raise ValueError("Ratings has duplicates, please fix it.")

    # Drop cache metadata column not needed in output
    if "integration_date" in ratings_df.columns:
        ratings_df.drop(columns=["integration_date"], inplace=True)

    column_order = [
        "slug", "user_rating", "liked", "title", "release_year",
        "letterboxd_avg_rating", "genres", "description", "tagline",
        "directors", "runtime", "imdb_id", "tmdb_id",
        "letterboxd_url", "imdb_url", "tmdb_url",
    ]
    existing_cols = [c for c in column_order if c in ratings_df.columns]
    extra_cols = [c for c in ratings_df.columns if c not in column_order]
    ratings_df = ratings_df[existing_cols + extra_cols]

    logger.info("Saving ratings data to %s", ratings_output_path)
    ratings_df.to_parquet(ratings_output_path, index=False)

    return ratings_df
