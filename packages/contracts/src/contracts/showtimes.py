"""Contract for ``showtimes.parquet``.

Produced by the standalone **Allocine-Showtimes-Scraping** repo; consumed inside
this monorepo by ``cinema_dashboard/utils/data_loader.py`` (the watchlist↔showtimes
join) and ``movies_management/modules/allocine_enrichment.py`` (cache expansion).
"""

from __future__ import annotations

from contracts.schema import ParquetContract

SHOWTIMES = ParquetContract(
    name="showtimes",
    required_columns=frozenset(
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
    ),
    notes=(
        "`runtime` is a raw display string like '1h 52min' (NOT parsed minutes); the "
        "dashboard derives `runtime_minutes` downstream. `release_year` is nullable. "
        "`director` may join multiple names with ' | '. `is_weekend` is also produced "
        "but the dashboard computes weekends itself, so it is not required here."
    ),
)
