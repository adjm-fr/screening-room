"""
User ratings data enrichment and export.

Merges user movie ratings with cached Letterboxd movie metadata to create
a comprehensive dataset combining personal ratings with global metadata.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def merge_ratings_with_letterboxd(
    films_dict: dict, data_letterboxd_df: pd.DataFrame, ratings_output_path: str
) -> pd.DataFrame:
    """
    Enrich user movie ratings with Letterboxd movie metadata.

    Converts user ratings data from Letterboxd API format to DataFrame, then merges
    with cached movie metadata. Handles column deduplication and enforces no-duplicates.

    Args:
        films_dict: Raw rated movies data from Letterboxd API. Expected structure:
                    {"movies": {"slug": {"name": str, "year": int, "rating": float,
                     "liked": bool, ...}}}
        data_letterboxd_df: Cached Letterboxd movie metadata DataFrame.
        ratings_output_path: Path to save enriched ratings (parquet format).

    Returns:
        Enriched ratings DataFrame with columns:
        slug, user_rating, liked, title, release_year, letterboxd_avg_rating,
        genres, description, tagline, directors, runtime, imdb_id, tmdb_id,
        letterboxd_url, imdb_url, tmdb_url.

    Raises:
        ValueError: If ratings data contains duplicate slugs.
    """
    movies = films_dict.get("movies", {})
    logger.info("Ratings has %d movies.", len(movies))

    # Convert ratings dict to DataFrame
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

    # Merge with cached movie metadata; left join to preserve all rated movies
    ratings_df = ratings_df.merge(
        data_letterboxd_df, on="slug", how="left", suffixes=("_user", "")
    )

    # Prioritize release_year from Letterboxd (more authoritative) with fallback to user data
    if "release_year_user" in ratings_df.columns:
        ratings_df["release_year"] = ratings_df["release_year"].fillna(
            ratings_df["release_year_user"]
        )
        ratings_df.drop(columns=["release_year_user"], inplace=True)

    # Drop redundant name field (title from Letterboxd is preferred and more complete)
    if "name" in ratings_df.columns:
        ratings_df.drop(columns=["name"], inplace=True)

    # Validate no duplicate entries by slug
    dup_count = ratings_df[ratings_df.duplicated("slug")].shape[0]
    if dup_count > 0:
        logger.error("Ratings has %d duplicates", dup_count)
        raise ValueError("Ratings has duplicates, please fix it.")

    # Remove cache metadata not needed in output
    if "integration_date" in ratings_df.columns:
        ratings_df.drop(columns=["integration_date"], inplace=True)

    # Reorder columns for consistent output (preferred columns first, extras at end)
    column_order = [
        "slug",
        "user_rating",
        "liked",
        "title",
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
    existing_cols = [c for c in column_order if c in ratings_df.columns]
    extra_cols = [c for c in ratings_df.columns if c not in column_order]
    ratings_df = ratings_df[existing_cols + extra_cols]

    logger.info("Saving ratings data to %s", ratings_output_path)
    ratings_df.to_parquet(ratings_output_path, index=False)

    return ratings_df
