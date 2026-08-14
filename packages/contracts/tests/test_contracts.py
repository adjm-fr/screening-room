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
            "keywords",
            "themes",
            "mini_themes",
            "directors",
            "producers",
            "writers",
            "cast",
            "composers",
            "trailer_url",
            "studio",
            "country",
            "origin_country",
            "language",
            "original_language",
            "integration_date",
            "source",
        }
    )


def test_data_letterboxd_requires_the_tmdb_territory_columns() -> None:
    # These five moved off Letterboxd's dynamic **details_by_type expansion onto the TMDB
    # bundle, where _fetch_movie seeds each as None on every row — so unlike before, their
    # presence is guaranteed and the contract can enforce it.
    for column in ("studio", "country", "origin_country", "language", "original_language"):
        assert column in DATA_LETTERBOXD.required_columns


def test_data_letterboxd_requires_the_tmdb_taxonomy_columns() -> None:
    # `genres` was always here; `keywords` joins it as the additive half of the same
    # migration group, seeded as None on every row by _fetch_movie so its presence is
    # guaranteed before any row has actually been migrated.
    for column in ("genres", "keywords"):
        assert column in DATA_LETTERBOXD.required_columns


def test_data_letterboxd_notes_keep_keywords_distinct_from_themes() -> None:
    # The trap: `keywords` looks like a replacement for themes/mini_themes and is not.
    # Both vocabularies coexist, so the notes must say which producer owns which.
    assert "keywords" in DATA_LETTERBOXD.notes
    assert "mini_themes" in DATA_LETTERBOXD.notes
    assert "TMDB_COLUMN_GROUPS" in DATA_LETTERBOXD.notes


def test_data_letterboxd_notes_distinguish_the_two_country_columns() -> None:
    # The whole reason this column move needed an analysis: production_countries and
    # origin_country are different fields, and the notes must say so.
    assert "production_countries" in DATA_LETTERBOXD.notes
    assert "origin_country" in DATA_LETTERBOXD.notes
    assert "original_title" in DATA_LETTERBOXD.notes
    assert isinstance(DATA_LETTERBOXD, ParquetContract)
