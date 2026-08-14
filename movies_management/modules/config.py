"""Centralised configuration for movies_management."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from common import AppSettings, make_settings_config
from pydantic import SecretStr, field_validator
from pydantic_settings import NoDecode


class TmdbColumnGroup(StrEnum):
    """One migratable block of cache columns that TMDB can produce instead of Letterboxd.

    The unit of migration is a *group*, not a column, because the columns in one group
    share a producer, a backfill and a taste consequence — and because they have to move
    together: ``country`` and ``language`` coming from different sources would leave the
    ranker keying on two vocabularies at once.

    Groups are enabled one at a time (see ``Settings.tmdb_column_groups``) so each one's
    effect on the taste ranker can be measured against the previous state in isolation.
    That is the whole reason this is a set of named groups rather than one boolean per
    migration: a boolean per group multiplies with every group added, and nothing stops
    two of them from being flipped in the same run and confounding the comparison.

    Adding a group means adding a member here, a branch in
    ``get_letterboxd_data._fetch_all``/``_fetch_movie``, and a row in CACHE_COLUMNS.md.
    """

    #: `studio`, `country`, `language` (swapped) + `origin_country`, `original_language` (new).
    TERRITORIES = "territories"
    #: `genres` (swapped) + `keywords` (new).
    GENRES = "genres"


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
    # Which column groups TMDB produces instead of Letterboxd — the one switch of the whole
    # TMDB migration, e.g. `TMDB_COLUMN_GROUPS=territories,genres`. Empty (the default) means
    # every column keeps the producer its cached rows were written from; each name added
    # hands one group over to TMDB and additionally populates that group's new columns,
    # which Letterboxd has no equivalent for and which stay null until then.
    #
    # One set rather than one boolean per group, because groups are migrated one at a time
    # to measure each one's effect on the taste ranker: a set makes "which groups are on"
    # a single value to record beside a backtest number, and makes adding the next group a
    # new enum member instead of a new setting threaded through five signatures.
    #
    # Every column is written in every position, so the parquet schema never depends on this
    # setting — only the values do. That is what makes a group safe to turn back off:
    # nothing downstream gains or loses a column, and the contract holds throughout.
    #
    # Enabling a group is a one-way step in practice, not in code: TMDB uses its own
    # spellings (`USA` -> `United States of America`), so a cache half-written under each
    # setting splits one taste-affinity key into two. Enable, then backfill every row in one
    # pass — see CACHE_COLUMNS.md.
    #
    # `NoDecode` + the validator below: pydantic-settings JSON-decodes complex fields before
    # validation runs, so a bare `frozenset` field would reject `territories,genres` with a
    # SettingsError no field validator could intercept. NoDecode hands the raw string over
    # instead. Unknown names raise (StrEnum coercion), deliberately: a typo'd group silently
    # reverting a migration is the exact failure mode `extra="ignore"` already hides for
    # misspelled *keys*, and it must not also hide for misspelled values.
    tmdb_column_groups: Annotated[frozenset[TmdbColumnGroup], NoDecode] = frozenset()

    @field_validator("tmdb_column_groups", mode="before")
    @classmethod
    def _split_group_names(cls, value: object) -> object:
        """Parse the comma-separated env form, tolerating whitespace and a trailing comma."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value
