"""Shared logging configuration for entry points.

Every entry point (movies ``main.py``, dashboard ``app.py`` / ``orchestrate.py``)
called ``logging.basicConfig(...)`` with a near-identical format and then quieted
``httpx`` (and a few others) so per-request INFO logs don't drown real output.
:func:`configure_logging` centralises that.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Libraries that log one INFO line per network call — quieted to WARNING by default.
_DEFAULT_QUIET = ("httpx",)


def configure_logging(
    level: str | int = "INFO",
    *,
    fmt: str = _DEFAULT_FORMAT,
    datefmt: str = _DEFAULT_DATEFMT,
    quiet: Iterable[str] = _DEFAULT_QUIET,
) -> logging.Logger:
    """Configure root logging and quiet noisy network loggers.

    ``level`` accepts a name (``"INFO"``, case-insensitive) or a numeric level.
    Returns the root logger for convenience.
    """
    resolved = level.upper() if isinstance(level, str) else level
    logging.basicConfig(level=resolved, format=fmt, datefmt=datefmt)
    for name in quiet:
        logging.getLogger(name).setLevel(logging.WARNING)
    return logging.getLogger()
