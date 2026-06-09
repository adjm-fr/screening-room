"""Centralised configuration for movies_management."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    output_path: Path
    letterboxd_days_to_update: int = 365
    letterboxd_refresh_limit: int | None = None
    tmdb_api_key: str = ""
