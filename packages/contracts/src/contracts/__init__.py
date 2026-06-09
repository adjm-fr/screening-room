"""Parquet schema contracts shared across the cinema pipeline.

These declare the columns each consumer depends on. Pair them with
``common.read_parquet_validated(..., required_columns=<contract>.required_columns)``
to turn an implicit, unenforced column contract into an early, explicit error.
"""

from contracts.schema import ParquetContract
from contracts.showtimes import SHOWTIMES

__all__ = ["ParquetContract", "SHOWTIMES"]
