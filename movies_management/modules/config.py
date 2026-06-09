"""Centralised configuration for movies_management."""

from pathlib import Path

from common import AppSettings, make_settings_config

_ROOT = Path(__file__).resolve().parents[1]


class Settings(AppSettings):
    model_config = make_settings_config(_ROOT)

    output_path: Path
    letterboxd_days_to_update: int = 365
    letterboxd_refresh_limit: int | None = None
    tmdb_api_key: str = ""
