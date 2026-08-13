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
            "studio",
            "country",
            "origin_country",
            "language",
            "original_language",
            "integration_date",
            "source",
        }
    ),
    notes=(
        "`studio`/`country`/`language` became required when they moved off Letterboxd's "
        "dynamic `**details_by_type` expansion onto TMDB "
        "(`production_companies`/`production_countries`/`spoken_languages`), joining the "
        "new `origin_country`/`original_language`: `_fetch_movie` now seeds all five as "
        "None on every row, so their presence is guaranteed the same way `composers`' is. "
        "Presence only — like every column here, they are nullable, and TMDB genuinely "
        "has no company or country on record for some obscure films. "
        "`country` and `origin_country` are NOT two spellings of one field: `country` is "
        "the full co-production territory list as display names (TMDB "
        "`production_countries`, ~1.47 entries/film), `origin_country` the nationality of "
        "the production as bare ISO 3166-1 codes (~1.14/film, no display names exist in "
        "the payload). They are identical 73.7% of the time and never disjoint. The taste "
        "ranker keys on `country`; `origin_country` is carried but not yet a dimension. "
        "`original_language` is an ISO 639-1 code, whereas `language` is a comma-joined "
        "list of English language names — also not the same vocabulary. "
        "`original_title` is null when it equals `title` — "
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
