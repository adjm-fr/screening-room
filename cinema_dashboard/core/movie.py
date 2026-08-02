"""
Data assembly for the movie detail page (``pages/movie.py``).

Streamlit-free and pure pandas on purpose: every function here takes already-
loaded DataFrames and returns plain pandas, so the page module stays a thin
renderer and this logic is unit-testable without a script run context.

``data_letterboxd.parquet`` is the single backing frame. It is a clean superset
of the ratings and watchlist parquets — every rated and every watchlisted slug
appears in it, plus a few hundred Allocine-enriched films in neither — so one
loader answers for any film the app can render a card for. ``slug`` is the key:
it is unique and non-null there, while ``tmdb_id`` carries both nulls and
duplicates and would collide as a route parameter.

Public API:
    load_movie(cache, ratings, slug)   -> the film's row + user_rating, or None
    movie_screenings(shows, slug)      -> that film's upcoming screenings
    similar_films(cache, movie, …)     -> same-director / shared-theme films,
                                          narrowed to the watchlist by the page
"""

from __future__ import annotations

import logging
from collections.abc import Collection

import pandas as pd

log = logging.getLogger(__name__)

#: Columns whose comma-separated cells describe a film's themes. Mirrors
#: ``core.taste._DIM_COLUMNS["themes"]`` — mini-themes are the same Letterboxd
#: vocabulary, split upstream by the scraper's "type" field.
THEME_COLUMNS = ("themes", "mini_themes")

#: A film needs this many themes in common to count as "more like this" on
#: theme evidence alone. One shared theme is near-meaningless (the vocabulary
#: has broad entries like "Relationship comedy"); a shared director always
#: qualifies regardless.
MIN_SHARED_THEMES = 2


def split_values(cell: object) -> list[str]:
    """Split a comma-separated metadata cell into stripped values (``[]`` when absent)."""
    if not isinstance(cell, str) or not cell:
        return []
    return [part.strip() for part in cell.split(",") if part.strip()]


def _values_from(row: pd.Series, columns: tuple[str, ...]) -> set[str]:
    """Union of :func:`split_values` across several columns of one row."""
    return {value for column in columns for value in split_values(row.get(column))}


def load_movie(cache_df: pd.DataFrame, ratings_df: pd.DataFrame, slug: str) -> pd.Series | None:
    """Return one film's full metadata row, or ``None`` when the slug is unknown.

    ``cache_df`` is ``data_letterboxd.parquet``. The user's own ``user_rating``
    is **left-joined** from ``ratings_df`` (the cache does not carry it), so an
    unrated film — watchlisted or merely cached — comes back with the column
    present and ``None``, and the caller can render "not rated yet" instead of
    branching on a missing key. A blank/unknown slug, an empty cache, or a
    cache without a ``slug`` column all return ``None`` so the page can show its
    empty state rather than raise.

    ``liked`` is deliberately not surfaced: it is pulled from letterboxdpy but
    never populated (all-zero), so it would read as "disliked everything".
    """
    if not slug or cache_df.empty or "slug" not in cache_df.columns:
        return None
    matches = cache_df[cache_df["slug"] == slug]
    if matches.empty:
        log.info("Movie detail requested for unknown slug %r", slug)
        return None

    movie = matches.iloc[0].copy()
    movie["user_rating"] = None
    if not ratings_df.empty and {"slug", "user_rating"} <= set(ratings_df.columns):
        rated = ratings_df.loc[ratings_df["slug"] == slug, "user_rating"]
        if not rated.empty and pd.notna(rated.iloc[0]):
            movie["user_rating"] = float(rated.iloc[0])
    return movie


def movie_screenings(shows_df: pd.DataFrame, slug: str) -> pd.DataFrame:
    """Return one film's rows from a films↔showtimes join, earliest screening first.

    Matches on ``letterboxd_slug`` (what
    :func:`sources.loader.build_watchlist_showtimes` names the key it carries
    through) and falls back to ``slug``. Returns an empty frame — never raises —
    when the join is empty or carries neither column, which is the normal case
    for a film that simply isn't screening.
    """
    if shows_df.empty:
        return shows_df
    slug_column = next((c for c in ("letterboxd_slug", "slug") if c in shows_df.columns), None)
    if slug_column is None or not slug:
        return shows_df.iloc[0:0]
    rows = shows_df[shows_df[slug_column] == slug]
    if rows.empty or "showtimes" not in rows.columns:
        return rows
    return rows.assign(_dt=pd.to_datetime(rows["showtimes"], errors="coerce")).sort_values("_dt").drop(columns=["_dt"])


def similar_films(
    cache_df: pd.DataFrame,
    movie: pd.Series,
    *,
    limit: int = 12,
    watchlist_slugs: Collection[str] | None = None,
) -> pd.DataFrame:
    """Return films from the cache sharing this one's director or themes.

    A row qualifies on a shared director (the strongest personal signal, and
    the dimension the taste ranker weights highest) or on at least
    :data:`MIN_SHARED_THEMES` shared themes. Ordering is director matches
    first, then theme overlap, then the community rating — so the rail leads
    with "more by this director" and fills out with tonally similar films.
    The film itself is always excluded. Returns an empty frame when the film
    has no usable director/theme metadata, so the caller omits the section
    rather than rendering an empty rail.

    ``watchlist_slugs`` narrows the pool to films the user still intends to
    watch, and the page always passes it when the watchlist parquet is there.
    It matters because the cache is a superset of the *ratings* parquet: left
    unfiltered on the real data, 78% of a rail is films already rated (i.e.
    already seen) against 20% watchlisted — the inverse of what a "more like
    this" rail is for. The watchlist alone still leaves a median ~99 candidates
    per film, well above ``limit``, and the ~19% of films it empties are ones
    the caller already omits the section for.
    """
    if cache_df.empty or "slug" not in cache_df.columns:
        return cache_df.iloc[0:0]

    directors = {d.casefold() for d in split_values(movie.get("directors"))}
    themes = {t.casefold() for t in _values_from(movie, THEME_COLUMNS)}
    if not directors and not themes:
        return cache_df.iloc[0:0]

    candidates = cache_df[cache_df["slug"] != movie.get("slug")]
    if watchlist_slugs is not None:
        candidates = candidates[candidates["slug"].isin(watchlist_slugs)]
    candidates = candidates.copy()
    if candidates.empty:
        return candidates

    candidates["_shared_director"] = (
        candidates["directors"].map(lambda cell: bool(directors & {d.casefold() for d in split_values(cell)}))
        if directors and "directors" in candidates.columns
        else False
    )
    candidates["_shared_themes"] = (
        candidates.apply(lambda row: len(themes & {t.casefold() for t in _values_from(row, THEME_COLUMNS)}), axis=1)
        if themes
        else 0
    )
    qualified = candidates[candidates["_shared_director"] | (candidates["_shared_themes"] >= MIN_SHARED_THEMES)]
    if qualified.empty:
        return qualified.drop(columns=["_shared_director", "_shared_themes"])

    sort_columns = ["_shared_director", "_shared_themes"]
    if "letterboxd_avg_rating" in qualified.columns:
        sort_columns.append("letterboxd_avg_rating")
    ranked = qualified.sort_values(sort_columns, ascending=False, na_position="last")
    return ranked.head(limit).drop(columns=["_shared_director", "_shared_themes"])
