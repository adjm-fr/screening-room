"""
Pure pandas statistics and table helpers behind the Movies Database page.

Streamlit-free by design ("a page renders, a core module decides" — same split
as :mod:`core.agenda` for the calendar page): everything here takes a DataFrame
and returns plain data, so it is unit-testable without importing the page.

Two vocabularies live here:

- **Ratings breakdown** — :data:`RATING_TIERS` (the semantic tier ladder the
  0–5 half-star scale encodes, mirroring :mod:`core.taste`'s documented
  methodology), :func:`rating_histogram`, :func:`rating_disagreements` /
  :func:`delta_summary` (you vs the Letterboxd community), and
  :func:`decade_profile` (``release_year`` is the only temporal axis — there
  is no watched/diary date anywhere in the data).
- **Tables tab** — :func:`filter_table` and the :data:`TABLE_PRESETS` column
  subsets, so the three raw parquet dumps become scannable.

Every function is total: a missing column or empty frame degrades to an empty
(or zero-filled) result, never a raise.
"""

from __future__ import annotations

import pandas as pd

# The user's rating scale is a semantic tier ladder, not a linear quality axis
# (see core/taste.py: the low mean ~2.5 is the scale's design, not harshness).
# Each tier spans two half-star steps; together they cover every half-star from
# 0.5 to 5.0 exactly once. Labels match format_taste_profile's pinned legend.
RATING_TIERS: tuple[tuple[float, float, str], ...] = (
    (0.5, 1.0, "Don't bother"),
    (1.5, 2.0, "Watchable"),
    (2.5, 3.0, "Good"),
    (3.5, 4.0, "Must watch"),
    (4.5, 5.0, "Masterpiece"),
)

# Every half-star a rating can take, in display order.
HALF_STARS: tuple[float, ...] = tuple(x / 2 for x in range(1, 11))


def rating_histogram(ratings_df: pd.DataFrame) -> pd.DataFrame:
    """Count films per half-star rating, zero-filling so all 10 bins are always present.

    Ratings are half-star quantized at the source (Letterboxd stars), so this
    is a ``value_counts`` + ``reindex``, not a binning: any off-grid value
    (there are none in practice) would simply not land in a bin. Returns a
    frame with ``rating`` (0.5 … 5.0) and ``count`` columns; an empty or
    column-less input yields the same shape with every count at zero, so the
    chart renderer never needs a special case.
    """
    if "user_rating" not in ratings_df.columns:
        counts = [0] * len(HALF_STARS)
    else:
        by_rating = ratings_df["user_rating"].dropna().value_counts()
        counts = [int(by_rating.get(star, 0)) for star in HALF_STARS]
    return pd.DataFrame({"rating": list(HALF_STARS), "count": counts})


_DISAGREEMENT_COLUMNS = ("slug", "poster_url", "title", "directors", "release_year")


def rating_disagreements(ratings_df: pd.DataFrame, *, n: int = 8) -> pd.DataFrame:
    """The films where your rating and the Letterboxd average diverge most, both directions.

    ``delta = user_rating − letterboxd_avg_rating``; rows missing either side
    are dropped. Returns up to ``n`` most-positive ("you liked it more") and
    ``n`` most-negative rows concatenated, sorted by ``delta`` descending, with
    a ``direction`` column (``"higher"`` / ``"lower"``). On a small history the
    two slices can overlap; duplicates keep their first (positive-side) row.
    """
    if "user_rating" not in ratings_df.columns or "letterboxd_avg_rating" not in ratings_df.columns:
        return pd.DataFrame(columns=[*_DISAGREEMENT_COLUMNS, "user_rating", "letterboxd_avg_rating", "delta", "direction"])
    keep = [c for c in _DISAGREEMENT_COLUMNS if c in ratings_df.columns]
    rated = ratings_df.dropna(subset=["user_rating", "letterboxd_avg_rating"])[
        [*keep, "user_rating", "letterboxd_avg_rating"]
    ].copy()
    rated["delta"] = rated["user_rating"] - rated["letterboxd_avg_rating"]
    higher = rated.sort_values("delta", ascending=False).head(n).assign(direction="higher")
    lower = rated.sort_values("delta", ascending=True).head(n).assign(direction="lower")
    out = pd.concat([higher, lower])
    out = out[~out.index.duplicated(keep="first")]
    return out.sort_values("delta", ascending=False).reset_index(drop=True)


def delta_summary(ratings_df: pd.DataFrame) -> dict[str, float]:
    """Aggregate the you-vs-Letterboxd gap: ``n`` comparable films, ``mean_delta``, ``share_below``.

    ``share_below`` is the fraction of comparable films rated under the
    community average — expected to be large here, because the user's tier
    ladder centers near 2.5 while Letterboxd averages cluster around 3.5; the
    caller's caption should pre-empt the "am I harsh?" misreading. ``n == 0``
    (no comparable rows) zeroes the other fields; guard on it before display.
    """
    if "user_rating" not in ratings_df.columns or "letterboxd_avg_rating" not in ratings_df.columns:
        return {"n": 0.0, "mean_delta": 0.0, "share_below": 0.0}
    rated = ratings_df.dropna(subset=["user_rating", "letterboxd_avg_rating"])
    if rated.empty:
        return {"n": 0.0, "mean_delta": 0.0, "share_below": 0.0}
    delta = rated["user_rating"] - rated["letterboxd_avg_rating"]
    return {
        "n": float(len(rated)),
        "mean_delta": float(delta.mean()),
        "share_below": float((delta < 0).mean()),
    }


def decade_profile(ratings_df: pd.DataFrame) -> pd.DataFrame:
    """Films rated per decade with the mean rating given, sorted chronologically.

    Decade is derived from ``release_year`` (1994 → 1990) — the same bucketing
    :mod:`core.taste` uses for its decade dimension. Junk / null years are
    dropped. Returns ``decade`` (int), ``count`` (int), ``mean_rating``
    (float, NaN when no row in the bucket carries a rating) — empty frame with
    those columns when nothing is usable.
    """
    empty = pd.DataFrame({"decade": pd.Series(dtype=int), "count": pd.Series(dtype=int), "mean_rating": pd.Series(dtype=float)})
    if "release_year" not in ratings_df.columns:
        return empty
    years = pd.to_numeric(ratings_df["release_year"], errors="coerce")
    usable = ratings_df.loc[years.notna()].copy()
    if usable.empty:
        return empty
    usable["decade"] = (years.loc[usable.index].astype(int) // 10 * 10).astype(int)
    if "user_rating" not in usable.columns:
        usable["user_rating"] = pd.Series(dtype=float)
    out = (
        usable.groupby("decade")
        .agg(count=("decade", "size"), mean_rating=("user_rating", "mean"))
        .reset_index()
        .sort_values("decade")
        .reset_index(drop=True)
    )
    out["count"] = out["count"].astype(int)
    return out


def explode_tags(series: pd.Series, separator: str = ", ") -> pd.Series:
    """Split comma-joined metadata cells into one trimmed, non-empty value per row."""
    return series.dropna().astype(str).str.split(separator).explode().str.strip().pipe(lambda s: s[s != ""])


# ── Tables tab ──────────────────────────────────────────────────────────────

_SEARCH_COLUMNS = ("title", "french_title", "original_title", "directors")

# Column subsets for the Tables tab, applied through preset_columns so a
# preset never KeyErrors on a frame that lacks some of its columns (the
# cache/ratings/watchlist frames differ — e.g. only ratings carries
# user_rating, and streaming_on/detail_url are appended by the page).
# "All" is the empty sentinel: show every column.
TABLE_PRESETS: dict[str, tuple[str, ...]] = {
    "Essentials": (
        "detail_url",
        "poster_url",
        "title",
        "directors",
        "release_year",
        "user_rating",
        "letterboxd_avg_rating",
        "runtime",
        "genres",
        "streaming_on",
    ),
    "Links": ("detail_url", "title", "letterboxd_url", "imdb_url", "tmdb_url"),
    "Full metadata": (
        "detail_url",
        "title",
        "original_title",
        "french_title",
        "release_year",
        "tagline",
        "description",
        "themes",
        "mini_themes",
        "keywords",
        "cast",
        "writers",
        "producers",
        "composers",
        "studio",
        "country",
        "origin_country",
        "language",
        "original_language",
        "source",
        "integration_date",
    ),
    "All": (),
}


def filter_table(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Case-insensitive substring filter over title spellings + directors.

    A blank query (or an empty frame) passes through untouched. The query is
    matched literally (``regex=False``), so ``C+`` or ``(500)`` need no
    escaping. Rows match when *any* search column contains the query.
    """
    trimmed = query.strip()
    if not trimmed or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for col in _SEARCH_COLUMNS:
        if col in df.columns:
            mask |= df[col].fillna("").astype(str).str.contains(trimmed, case=False, regex=False)
    return df[mask]


def preset_columns(df: pd.DataFrame, preset: str) -> list[str]:
    """Resolve a :data:`TABLE_PRESETS` name to the columns actually present in ``df``.

    Unknown preset names and the empty "All" sentinel yield every column, and
    so does a preset whose columns are all absent — an empty table is never
    the right answer to a preset choice.
    """
    wanted = TABLE_PRESETS.get(preset, ())
    present = [c for c in wanted if c in df.columns]
    return present if present else list(df.columns)
