"""Centralised configuration for movies_management."""

from pathlib import Path

from common import AppSettings, make_settings_config


class Settings(AppSettings):
    model_config = make_settings_config()

    output_path: Path
    letterboxd_days_to_update: int = 365
    # Max stale movies to refresh per run. Defaults to 1000; raise it (or set a
    # very large value) to lift the cap. None also means uncapped.
    letterboxd_refresh_limit: int | None = 1000
    tmdb_api_key: str = ""
