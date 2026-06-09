"""Tests for the parquet contracts."""

from __future__ import annotations

import dataclasses

import pytest

from contracts import SHOWTIMES, ParquetContract


def test_showtimes_declares_the_consumed_columns() -> None:
    # The exact set both consumers (dashboard data_loader + movies allocine
    # enrichment) depend on. If a consumer starts reading a new column, add it
    # here so the producer side is held to it.
    assert SHOWTIMES.required_columns == frozenset(
        {
            "theater_id",
            "theater_name",
            "movie",
            "original_title",
            "director",
            "runtime",
            "release_year",
            "showtimes",
        }
    )


def test_contract_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        SHOWTIMES.name = "other"  # type: ignore[misc]


def test_notes_flag_the_runtime_string_quirk() -> None:
    assert "runtime" in SHOWTIMES.notes
    assert isinstance(SHOWTIMES, ParquetContract)
