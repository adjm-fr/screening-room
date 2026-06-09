"""Lightweight, dependency-free schema descriptor for parquet contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParquetContract:
    """Declares the columns a consumer depends on for a given parquet.

    ``required_columns`` is the enforced contract; ``notes`` documents
    producer-side quirks (nullable fields, raw-string formats) that consumers
    must account for. Kept dependency-free so the standalone Allocine producer
    can mirror it without pulling pandas/pydantic.
    """

    name: str
    required_columns: frozenset[str]
    notes: str = ""
