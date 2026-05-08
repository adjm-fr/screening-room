"""
Dagster pipeline for the cinema dashboard.

Launch the UI with:
    dagster dev -m pipeline.definitions

Required env vars (same as orchestrate.py + .env):
    ALLOCINE_DIR            path to the Allocine-Showtimes-Scraping repo
    MOVIES_DIR              path to the movies_management repo
    ALLOCINE_OUTPUT_PATH    path to the output showtimes parquet file
    MOVIES_OUTPUT_PATH      directory that contains the watchlist parquet files
"""

from dagster import Definitions, define_asset_job, load_assets_from_modules

from modules.config import settings

from . import assets
from .resources import ScraperConfig

_all_assets = load_assets_from_modules([assets])

defs = Definitions(
    assets=_all_assets,
    jobs=[
        define_asset_job("showtimes_job", selection=["showtimes"]),
        define_asset_job("watchlist_job", selection=["watchlist"]),
        define_asset_job("all_scrapers_job", selection=["showtimes", "watchlist"]),
    ],
    resources={
        "scraper_config": ScraperConfig(
            allocine_dir=str(settings.allocine_dir),
            movies_dir=str(settings.movies_dir),
            allocine_output_path=str(settings.allocine_output_path or ""),
            movies_output_path=str(settings.movies_output_path or ""),
        ),
    },
)
