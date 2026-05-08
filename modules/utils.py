"""Utility functions for movie data transformation and I/O."""

import asyncio
import logging
import os

import pandas as pd
from letterboxdpy.user import User

logger = logging.getLogger(__name__)


async def _fetch_user_data(user: User) -> tuple[dict, dict]:
    films, watchlist = await asyncio.gather(
        asyncio.to_thread(user.get_films),
        asyncio.to_thread(user.get_watchlist),
        return_exceptions=True,
    )
    if isinstance(films, BaseException):
        logger.error("Failed to fetch films: %s", films)
        raise films
    if isinstance(watchlist, BaseException):
        logger.error("Failed to fetch watchlist: %s", watchlist)
        raise watchlist
    return films, watchlist


def fetch_user_data(user: User) -> tuple[dict, dict]:
    """Fetch films and watchlist concurrently for a Letterboxd user."""
    return asyncio.run(_fetch_user_data(user))


def build_movies_df(films_dict: dict, watchlist_dict: dict) -> pd.DataFrame:
    """Build a unified DataFrame from Letterboxd films and watchlist dicts."""
    ratings_rows = [
        {
            "slug": slug,
            "user_rating": info.get("rating"),
            "liked": info.get("liked"),
            "name": info.get("name"),
            "release_year": info.get("year"),
            "source": "ratings",
        }
        for slug, info in films_dict.get("movies", {}).items()
    ]
    watchlist_rows = [
        {
            "slug": info["slug"],
            "name": info.get("name"),
            "release_year": info.get("year"),
            "source": "watchlist",
        }
        for info in watchlist_dict.get("data", {}).values()
        if "slug" in info
    ]
    return pd.DataFrame(ratings_rows + watchlist_rows)


def merge_letterboxd_metadata(all_movies_df: pd.DataFrame, data_letterboxd_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join user movie data with the Letterboxd metadata cache on slug."""
    merged = all_movies_df.merge(data_letterboxd_df, on="slug", how="left", suffixes=("_user", ""))
    if "release_year_user" in merged.columns:
        merged["release_year"] = merged["release_year"].fillna(merged["release_year_user"]).infer_objects()
        merged.drop(columns=["release_year_user"], inplace=True)
    for col in ("name", "integration_date"):
        if col in merged.columns:
            merged.drop(columns=[col], inplace=True)
    return merged


def reorder_columns(df: pd.DataFrame, column_order: list[str]) -> pd.DataFrame:
    """Return df with columns in column_order first, then any remaining columns."""
    existing = [c for c in column_order if c in df.columns]
    extra = [c for c in df.columns if c not in column_order]
    return df[existing + extra]


def find_stale_slugs(df: pd.DataFrame, days_to_update: int, now: pd.Timestamp) -> list[str]:
    """Return slugs whose integration_date is older than days_to_update days."""
    age_days = (now - df["integration_date"]).dt.days
    return df[age_days > days_to_update]["slug"].tolist()


def save_parquet(df: pd.DataFrame, column_order: list[str], path: str | os.PathLike) -> None:
    """Reorder columns and write df to parquet at path."""
    reorder_columns(df, column_order).to_parquet(path, index=False)
