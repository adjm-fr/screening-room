"""Shared pydantic-settings base for every monorepo member.

Each member previously re-declared the identical ``SettingsConfigDict`` (load
``<root>/.env``, ignore unknown keys). That boilerplate lives here now; members
subclass :class:`AppSettings` and add their own typed fields.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def make_settings_config(env_root: Path) -> SettingsConfigDict:
    """Standard settings config: read ``<env_root>/.env`` (utf-8), ignore extras.

    ``env_root`` is the member's repo root — conventionally
    ``Path(__file__).resolve().parents[1]`` from a member's ``modules/config.py``.
    """
    return SettingsConfigDict(env_file=env_root / ".env", env_file_encoding="utf-8", extra="ignore")


class AppSettings(BaseSettings):
    """Base class for each member's ``Settings``.

    Subclass it, set ``model_config = make_settings_config(_ROOT)``, and add the
    member's typed fields::

        _ROOT = Path(__file__).resolve().parents[1]

        class Settings(AppSettings):
            model_config = make_settings_config(_ROOT)
            output_path: Path
    """
