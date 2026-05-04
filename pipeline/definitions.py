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

import os
from pathlib import Path

from dagster import Definitions, define_asset_job, load_assets_from_modules
from dotenv import load_dotenv

from . import assets
from .resources import ScraperConfig

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

# Resolve path defaults (same fallback convention as orchestrate.py).
_allocine_dir = os.getenv("ALLOCINE_DIR", str(_ROOT.parent / "Allocine-Showtimes-Scraping"))
_movies_dir = os.getenv("MOVIES_DIR", str(_ROOT.parent / "movies_management"))
_allocine_output = os.getenv("ALLOCINE_OUTPUT_PATH", "")
_movies_output = os.getenv("MOVIES_OUTPUT_PATH", "")

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
            allocine_dir=_allocine_dir,
            movies_dir=_movies_dir,
            allocine_output_path=_allocine_output,
            movies_output_path=_movies_output,
        ),
    },
)
