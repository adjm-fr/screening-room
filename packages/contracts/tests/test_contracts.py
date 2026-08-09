"""Tests for the parquet contracts."""

from __future__ import annotations

import dataclasses

import pytest
from contracts import DATA_LETTERBOXD, SHOWTIMES, ParquetContract


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


def test_data_letterboxd_declares_the_stable_core_columns() -> None:
    # The stable core movies_management always writes, verified against the real
    # 6,751-row cache. studio/country/language are deliberately excluded — see
    # test_data_letterboxd_notes_flag_the_dynamic_detail_columns below.
    assert DATA_LETTERBOXD.required_columns == frozenset(
        {
            "slug",
            "movie_id",
            "letterboxd_url",
            "imdb_id",
            "tmdb_id",
            "imdb_url",
            "tmdb_url",
            "title",
            "french_title",
            "original_title",
            "release_year",
            "runtime",
            "tagline",
            "description",
            "letterboxd_avg_rating",
            "poster_url",
            "banner_url",
            "genres",
            "themes",
            "mini_themes",
            "directors",
            "producers",
            "writers",
            "cast",
            "trailer_url",
            "integration_date",
            "source",
        }
    )


def test_data_letterboxd_excludes_dynamic_detail_columns() -> None:
    # studio/country/language come from **details_by_type and aren't guaranteed
    # on every row, unlike the stable core above.
    assert "studio" not in DATA_LETTERBOXD.required_columns
    assert "country" not in DATA_LETTERBOXD.required_columns
    assert "language" not in DATA_LETTERBOXD.required_columns


def test_data_letterboxd_notes_flag_the_dynamic_detail_columns() -> None:
    assert "details_by_type" in DATA_LETTERBOXD.notes
    assert "original_title" in DATA_LETTERBOXD.notes
    assert isinstance(DATA_LETTERBOXD, ParquetContract)
