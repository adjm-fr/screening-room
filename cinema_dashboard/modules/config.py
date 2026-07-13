"""
Centralised configuration for the cinema dashboard.

All env vars are declared here. Required fields raise a ValidationError at
import time; optional fields default to None or a sensible value.

Paths are typed as ``Path | None`` so page code can test ``if not path``
and render a user-friendly Streamlit error rather than crashing the app.

Kept cheap to import (stdlib + pydantic-settings only, via ``common``); the
parquet/pandas helpers live in ``common.parquet_io`` and are imported by data
loaders, not here, so this very-hot import path stays light.
"""

from pathlib import Path

from common import AppSettings, make_settings_config
from pydantic import Field

_ROOT = Path(__file__).resolve().parents[1]


class Settings(AppSettings):
    model_config = make_settings_config()

    # Logging verbosity for the entry points (app.py, orchestrate.py). Defaults to
    # INFO so the served app doesn't emit per-rerun debug spam; set LOG_LEVEL=DEBUG
    # to trace the render/join hot paths during development.
    log_level: str = "INFO"

    # Data paths — optional so individual pages degrade gracefully when unset.
    # movies_output_path reads OUTPUT_PATH: the single shared key that
    # movies_management writes its parquets to (one source of truth in the
    # workspace .env — this dir holds the three *_letterboxd.parquet files).
    movies_output_path: Path | None = Field(default=None, validation_alias="OUTPUT_PATH")
    allocine_output_path: Path | None = None
    allocine_input_path: Path | None = None

    # Scraper repo locations (used by orchestrate.py and pipeline/definitions.py).
    # movies_management is now an in-repo workspace sibling. Allocine stays a
    # standalone repo *outside* this monorepo, so its default location is one level
    # further up; override with the ALLOCINE_DIR env var.
    allocine_dir: Path = _ROOT.parent.parent / "Allocine-Showtimes-Scraping"
    movies_dir: Path = _ROOT.parent / "movies_management"

    # Letterboxd config (mirrors movies_management/.env)
    letterboxd_username: str | None = None
    letterboxd_days_to_update: int = 365

    # Gemini (Recommendations page)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_max_tokens: int = 1024
    gemini_temperature: float = 0.2
    gemini_top_p: float = 0.8

    # TMDB watch providers
    tmdb_api_key: str | None = None
    streaming_services: str = ""  # comma-separated provider slugs the user subscribes to

    @property
    def streaming_service_slugs(self) -> set[str]:
        """Parsed, slugified set of subscribed services (flatrate providers only).

        Free providers (Arte.tv, France.tv, …) are watchable by everyone and are
        not gated by this set — see ``utils.streaming.STREAMING_COLUMNS``. The
        import is local (not a circular-import workaround — ``utils.streaming``
        never imports this module). ``modules.config`` is imported very early and
        very widely (orchestrate, every page, the Dagster pipeline, tests) and
        deliberately depends only on ``pathlib`` + ``pydantic-settings``. A
        top-level import would pull ``streamlit``/``pandas``/``requests`` into
        that hot path just to read env vars; deferring it keeps the cost on the
        UI/data-layer callers while still single-sourcing the slug rule in
        ``utils.streaming``.
        """
        from utils.streaming import _slugify

        return {_slugify(s) for s in self.streaming_services.split(",") if s.strip()}


settings = Settings()
