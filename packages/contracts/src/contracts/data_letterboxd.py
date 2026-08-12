"""Contract for ``data_letterboxd.parquet``.

Produced by ``movies_management`` (``main.py``'s cache write and
``modules/allocine_enrichment.py``'s ``enrich_cache_from_showtimes``); consumed
inside this monorepo by ``cinema_dashboard/sources/loader.py``,
``cinema_dashboard/core/movie.py`` and ``cinema_dashboard/sources/discover.py``.
"""

from __future__ import annotations

from contracts.schema import ParquetContract

DATA_LETTERBOXD = ParquetContract(
    name="data_letterboxd",
    required_columns=frozenset(
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
            "composers",
            "trailer_url",
            "integration_date",
            "source",
        }
    ),
    notes=(
        "`studio`/`country`/`language` are deliberately NOT required: `_fetch_movie` "
        "expands them via `**details_by_type` from whatever Letterboxd detail types a "
        "given film happens to carry, so they are present on most rows but not "
        "guaranteed on every row. `original_title` is null when it equals `title` — "
        "Letterboxd omits it rather than repeating it (~33% populated in the real cache; "
        'a null means "same as title", NOT missing data). `letterboxd_avg_rating` (and '
        "the joined-on `user_rating` in ratings_with_letterboxd.parquet) are on a 0-5 "
        "scale, not 0-10. `source` is one of `ratings` / `watchlist` / "
        "`allocine_showtimes` (see main.py's `assign_cache_source` and "
        "allocine_enrichment's own stamp). The `liked` column carried in "
        "ratings_with_letterboxd.parquet is all-zero and unused — pulled from "
        "letterboxdpy but never populated. `composers` is sourced from TMDB job "
        '"Original Music Composer" only, and is legitimately null on ~26% of films '
        "(no original score) — that null is data, not incompleteness, so it must NOT "
        "become a refresh trigger (main.py refreshes on age alone, never on a null "
        'column). Its column presence is guaranteed regardless: `_fetch_movie` seeds `"composers": '
        "None` on every row, TMDB credits or not."
    ),
)
