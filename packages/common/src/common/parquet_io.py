"""Validated parquet read/write helpers shared across members.

The parquet column schemas are the integration contract between the scrapers and
the dashboard, and were historically enforced nowhere — an upstream column rename
surfaced as a downstream ``KeyError`` or a silently empty join. These helpers make
the contract explicit: pass the columns a caller depends on (e.g.
``contracts.SHOWTIMES.required_columns``) and get a clear, early error if the
parquet is missing one.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd


class SchemaValidationError(ValueError):
    """Raised when a parquet is missing columns the caller declared it requires."""


def _check(df: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = set(required) - set(df.columns)
    if missing:
        present = sorted(str(c) for c in df.columns)
        raise SchemaValidationError(f"{label}: missing required columns {sorted(missing)} (present: {present})")


def read_parquet_validated(
    path: str | Path,
    *,
    required_columns: Iterable[str] | None = None,
    label: str = "parquet",
) -> pd.DataFrame:
    """Read a parquet and assert it carries ``required_columns`` (if given)."""
    df = pd.read_parquet(path)
    if required_columns is not None:
        _check(df, required_columns, label)
    return df


def write_parquet_validated(
    df: pd.DataFrame,
    path: str | Path,
    *,
    required_columns: Iterable[str] | None = None,
    label: str = "parquet",
) -> None:
    """Validate ``required_columns`` (if given), create parent dirs, and write.

    Writes with ``index=False`` to match the existing producers' convention.
    """
    if required_columns is not None:
        _check(df, required_columns, label)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
