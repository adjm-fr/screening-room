"""Shared library for the cinema monorepo.

``AppSettings``/``make_settings_config`` (settings) and ``configure_logging``
(logging) are exported here — they're cheap to import (stdlib + pydantic-settings
only), which matters because ``modules.config`` is on a very-hot import path.

The parquet helpers live in :mod:`common.parquet_io` and are imported directly by
data loaders. They pull in pandas, so they are deliberately **not** re-exported
here, keeping the config import path pandas-free.
"""

from common.logging import configure_logging
from common.settings import AppSettings, make_settings_config

__all__ = ["AppSettings", "make_settings_config", "configure_logging"]
