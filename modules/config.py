"""
Centralised configuration for the cinema dashboard.

All env vars are declared here. Required fields raise a ValidationError at
import time; optional fields default to None or a sensible value.

Paths are typed as ``Path | None`` so page code can test ``if not path``
and render a user-friendly Streamlit error rather than crashing the app.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Data paths — optional so individual pages degrade gracefully when unset
    movies_output_path: Path | None = None
    allocine_output_path: Path | None = None
    allocine_input_path: Path | None = None

    # Scraper repo locations (used by orchestrate.py and pipeline/definitions.py)
    allocine_dir: Path = _ROOT.parent / "Allocine-Showtimes-Scraping"
    movies_dir: Path = _ROOT.parent / "movies_management"

    # Letterboxd config (mirrors movies_management/.env)
    letterboxd_username: str | None = None
    letterboxd_days_to_update: int = 365

    # Hugging Face (Recommendations page)
    hf_api_key: str | None = None
    hf_model: str = "Qwen/Qwen2.5-72B-Instruct"
    hf_max_tokens: int = 1024


settings = Settings()
