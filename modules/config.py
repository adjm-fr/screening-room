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
    hf_model: str = "moonshotai/Kimi-K2-Instruct"
    hf_max_tokens: int = 1024
    hf_temperature: float = 0.2
    hf_top_p: float = 0.8

    # TMDB watch providers
    tmdb_api_key: str | None = None
    streaming_services: str = ""  # comma-separated provider slugs the user subscribes to

    @property
    def streaming_service_slugs(self) -> set[str]:
        """Parsed, slugified set of subscribed services (consumed in Phase 3).

        The import is local (not a circular-import workaround — ``utils.streaming``
        never imports this module). ``modules.config`` is imported very early and
        very widely (orchestrate, every page, the Dagster pipeline, tests) and
        deliberately depends only on ``pathlib`` + ``pydantic-settings``. A
        top-level import would pull ``streamlit``/``pandas``/``requests`` into
        that hot path just to read env vars; deferring it keeps the cost on the
        Phase-3-only caller while still single-sourcing the slug rule in
        ``utils.streaming``.
        """
        from utils.streaming import _slugify

        return {_slugify(s) for s in self.streaming_services.split(",") if s.strip()}


settings = Settings()
