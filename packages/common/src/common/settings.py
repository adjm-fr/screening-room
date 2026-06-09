"""Shared pydantic-settings base for every monorepo member.

Each member previously re-declared the identical ``SettingsConfigDict`` (load
``<member-root>/.env``, ignore unknown keys). That boilerplate lives here now;
members subclass :class:`AppSettings` and add their own typed fields.

Env vars are read from a **single workspace-root ``.env``** shared by every
member. :func:`make_settings_config` locates that root automatically (the
ancestor whose ``pyproject.toml`` declares ``[tool.uv.workspace]``), so members
call ``make_settings_config()`` with no arguments. ``extra="ignore"`` means each
member silently skips keys it doesn't declare, so the one file can hold the union
of every member's vars without collisions.
"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@cache
def find_workspace_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` to the workspace root.

    The root is the nearest ancestor whose ``pyproject.toml`` declares
    ``[tool.uv.workspace]``. Defaults to walking up from this module's location;
    since ``common`` is itself a workspace member, that always resolves to the
    one shared root regardless of which member called in. Cached because it does
    filesystem reads and the answer is constant for the life of the process.
    """
    origin = (start or Path(__file__)).resolve()
    for candidate in origin.parents:
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            if "workspace" in data.get("tool", {}).get("uv", {}):
                return candidate
    raise FileNotFoundError("Could not locate the workspace root: no ancestor pyproject.toml declares [tool.uv.workspace].")


def make_settings_config(env_root: Path | None = None) -> SettingsConfigDict:
    """Standard settings config: read ``<root>/.env`` (utf-8), ignore extras.

    With no argument the env file is the shared workspace-root ``.env`` (located
    via :func:`find_workspace_root`). Pass ``env_root`` only to override that
    (e.g. in a test fixture pointing at a ``tmp_path``).
    """
    root = env_root if env_root is not None else find_workspace_root()
    return SettingsConfigDict(env_file=root / ".env", env_file_encoding="utf-8", extra="ignore")


class AppSettings(BaseSettings):
    """Base class for each member's ``Settings``.

    Subclass it, set ``model_config = make_settings_config()``, and add the
    member's typed fields::

        class Settings(AppSettings):
            model_config = make_settings_config()
            output_path: Path
    """
