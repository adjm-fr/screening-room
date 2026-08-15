"""Centralised configuration for movies_management."""

from pathlib import Path

from common import AppSettings, make_settings_config
from pydantic import SecretStr


class Settings(AppSettings):
    model_config = make_settings_config()

    output_path: Path
    letterboxd_days_to_update: int = 365
    # Max stale movies to refresh per run. Defaults to 1000; raise it (or set a
    # very large value) to lift the cap. None also means uncapped.
    letterboxd_refresh_limit: int | None = 1000
    # Required: TMDB is the sole producer of french_title/cast/the crew columns/
    # trailer_url, and — since the territories/genres migration completed and the
    # cache was backfilled in one pass (Aug 2026) — of studio/country/origin_country/
    # language/original_language/genres/keywords too. A missing key used to degrade
    # eleven-plus columns to null; it now means `directors` (the taste ranker's
    # highest-weighted dimension and what confirms the watchlist<->showtimes join) and
    # every migrated column go null, so this fails fast at startup instead.
    # SecretStr so the credential cannot be printed by accident (str/repr/f-string all
    # render "**********"). Unwrapped with .get_secret_value() in main.py, the one place
    # that sends it over the wire.
    tmdb_api_key: SecretStr
