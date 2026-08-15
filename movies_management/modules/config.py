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
    # SecretStr so the credential cannot be printed by accident (str/repr/f-string all
    # render "**********"). Unwrapped with .get_secret_value() in main.py, the one place
    # that sends it over the wire. bool() still reflects emptiness, so `if not key` holds.
    tmdb_api_key: SecretStr = SecretStr("")
    # Which producer fills `studio`/`country`/`language` — the one switch of the territory
    # migration. False (default) keeps Letterboxd's details tab, the source every cached row
    # was written from; True switches to TMDB's production_companies/production_countries/
    # spoken_languages and additionally populates `origin_country`/`original_language`, which
    # Letterboxd has no equivalent for and which stay null under the default.
    #
    # Both paths write all five columns either way, so the parquet schema does not depend on
    # this flag — only the values do. That is what makes it safe to flip back: nothing
    # downstream gains or loses a column, and the contract holds in both positions.
    #
    # Flipping it to True is a one-way step in practice, not in code: TMDB uses its own
    # spellings (`USA` -> `United States of America`), so a cache half-written under each
    # setting splits one taste-affinity key into two. Flip it, then backfill every row in one
    # pass — see CACHE_COLUMNS.md.
    use_tmdb_territories: bool = False
