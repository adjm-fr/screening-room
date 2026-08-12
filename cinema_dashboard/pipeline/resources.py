from __future__ import annotations

from typing import TYPE_CHECKING

from common import reveal
from dagster import ConfigurableResource

if TYPE_CHECKING:
    from config import Settings


class ScraperConfig(ConfigurableResource):
    """Paths and directories needed by the two scraper subprocesses."""

    allocine_dir: str
    movies_dir: str
    allocine_output_path: str
    movies_output_path: str
    letterboxd_username: str
    tmdb_api_key: str

    @classmethod
    def from_settings(cls, settings: Settings) -> ScraperConfig:
        """Build the resource from the shared pydantic ``Settings`` instance.

        Optional paths/username default to "" so Dagster assets fail with a
        clear message rather than a coercion error when env vars are unset.

        ``tmdb_api_key`` goes through :func:`common.reveal`, **not** ``str()`` like its
        neighbours: it is a ``SecretStr``, so ``str()`` would yield ``"**********"`` and
        hand Dagster a literal mask to authenticate with — a silent 401 rather than a
        crash. This resource is the wire boundary, so revealing here is the point.
        """
        return cls(
            allocine_dir=str(settings.allocine_dir),
            movies_dir=str(settings.movies_dir),
            allocine_output_path=str(settings.allocine_output_path or ""),
            movies_output_path=str(settings.movies_output_path or ""),
            letterboxd_username=str(settings.letterboxd_username or ""),
            tmdb_api_key=reveal(settings.tmdb_api_key) or "",
        )
