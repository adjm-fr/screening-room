"""
Watchlist data enrichment and export.

Merges user watchlist data with cached Letterboxd movie metadata to create
an enriched dataset with complete movie information.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def merge_watchlist_with_letterboxd(
    watchlist_dict: dict, data_letterboxd_df: pd.DataFrame, watchlist_output_path: str
) -> pd.DataFrame:
    """
    Enrich user watchlist with Letterboxd movie metadata.

    Converts watchlist data from Letterboxd API format to DataFrame, then merges
    with cached movie metadata. Handles column deduplication and enforces no-duplicates.

    Args:
        watchlist_dict: Raw watchlist data from Letterboxd API. Expected structure:
                        {"data": {"slug": {"slug": str, "name": str, "year": int, ...}}}
        data_letterboxd_df: Cached Letterboxd movie metadata DataFrame.
        watchlist_output_path: Path to save enriched watchlist (parquet format).

    Returns:
        Enriched watchlist DataFrame with columns:
        slug, title, release_year, letterboxd_avg_rating, genres, description,
        tagline, directors, runtime, imdb_id, tmdb_id, letterboxd_url, imdb_url, tmdb_url.

    Raises:
        ValueError: If watchlist contains duplicate slugs.
    """
    movies = watchlist_dict.get("data", {})
    logger.info("Watchlist has %d movies.", len(movies))

    # Convert watchlist dict to DataFrame
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

    # Merge with cached movie metadata; left join to preserve all watchlist items
    watchlist_df = watchlist_df.merge(
        data_letterboxd_df, on="slug", how="left", suffixes=("_user", "")
    )

    # Prioritize release_year from Letterboxd (more authoritative) with fallback to user data
    if "release_year_user" in watchlist_df.columns:
        watchlist_df["release_year"] = watchlist_df["release_year"].fillna(
            watchlist_df["release_year_user"]
        )
        watchlist_df.drop(columns=["release_year_user"], inplace=True)

    # Drop redundant name field (title from Letterboxd is preferred)
    if "name" in watchlist_df.columns:
        watchlist_df.drop(columns=["name"], inplace=True)

    # Validate no duplicate entries by slug
    dup_count = watchlist_df[watchlist_df.duplicated("slug")].shape[0]
    if dup_count > 0:
        logger.error("Watchlist has %d duplicates", dup_count)
        raise ValueError("Watchlist has duplicates, please fix it.")

    # Remove cache metadata not needed in output
    if "integration_date" in watchlist_df.columns:
        watchlist_df.drop(columns=["integration_date"], inplace=True)

    # Reorder columns for consistent output (preferred columns first, extras at end)
    column_order = [
        "slug",
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
    existing_cols = [c for c in column_order if c in watchlist_df.columns]
    extra_cols = [c for c in watchlist_df.columns if c not in column_order]
    watchlist_df = watchlist_df[existing_cols + extra_cols]

    logger.info("Saving watchlist data to %s", watchlist_output_path)
    watchlist_df.to_parquet(watchlist_output_path, index=False)

    return watchlist_df
