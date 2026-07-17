"""
Taste ranker: induce a per-film match score from the Letterboxd ratings history.

The model is a weighted heuristic (no ML — single user, a few thousand
ratings). It works in three steps:

1. **Affinity profile** (:func:`build_affinity`). For every feature value the
   user has encountered — each director, genre, theme (mini-themes folded in),
   actor, country, language and decade — compute a signed, shrunk affinity::

       A(v) = Σ(rating_i − μ_user) / (n_v + SHRINKAGE_K)

   Centering on the *user's own mean* turns low ratings into genuine negative
   signal (a harsh rater's 3.5 is praise); the additive shrinkage constant
   damps single-film evidence (±(r−μ)/(1+k) max) while a rated body of work
   keeps most of its raw deviation.

2. **Film score** (:func:`score_films`). Per dimension, average the affinities
   of the film's *known* values only — unknown values are neutral, never a
   penalty — then blend with fixed weights plus a small community-quality
   prior, and map through a fixed logistic to a 0–100 match value::

       raw   = Σ_d WEIGHTS[d]·S_d + QUALITY_WEIGHT·(letterboxd_avg − QUALITY_CENTER)
       match = 100 / (1 + exp(−raw / LOGISTIC_TAU))

   The logistic depends only on (profile, film), so a film's match value is
   stable week to week regardless of what else is screening.

3. **Explanation** (:func:`explain`). The top strictly-positive contributions
   power honest "Because: …" chips — never "because you hate X".

Public API:
    TasteProfile               frozen profile (mu, n_ratings, affinities, counts)
    build_affinity(df)         ratings DataFrame -> TasteProfile
    score_films(df, profile)   -> 0–100 match Series, index-aligned
    explain(row, profile)      -> top-k positive (label, contribution) pairs
    attach_match(df, wl, p)    score the watchlist, left-join match by tmdb_id
    format_taste_profile(p)    compact summary string for the LLM system prompt
"""

from __future__ import annotations

import dataclasses
import logging
import math

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

# Mirrors data_loader.DATA_TTL_SECONDS; not imported because the dependency
# points the other way (data_loader delegates its profile string to us).
TTL_SECONDS = 300

# Shrinkage constant k in A(v) = Σ(r−μ)/(n+k): a singleton caps at ±(r−μ)/(1+k)
# while n=10 keeps 2/3 of the raw deviation. Uniform across dimensions — for
# genres/themes/decades n is in the hundreds, so k is negligible there.
SHRINKAGE_K = 5.0

# Per-dimension blend weights, sized so each dimension's weighted reach is
# comparable on real data: directors dominate when present (strongest personal
# signal); decade only tie-breaks (partly confounds curated-classics viewing).
# Cast sits between themes and decade (leads carry real signal but a film has
# up to 8 of them); country/language are weak context signals. The 2.2 backtest
# sweep will revisit these provisional values.
WEIGHTS: dict[str, float] = {
    "directors": 1.0,
    "genres": 0.6,
    "themes": 0.5,
    "cast": 0.4,
    "decade": 0.3,
    "country": 0.2,
    "language": 0.15,
}

# Community-quality prior: small, centered at the watchlist's median Letterboxd
# rating so the term stays signed instead of being a constant positive offset.
QUALITY_WEIGHT = 0.2
QUALITY_CENTER = 3.5

# Logistic temperature for the 0–100 display mapping, calibrated on the real
# watchlist so scores span the badge range (p5≈37, p50≈61, p95≈89) without
# saturating at either end.
LOGISTIC_TAU = 0.5

# Columns feeding each affinity dimension. Themes fold in mini_themes (same
# Letterboxd vocabulary, split upstream by the scraper's "type" field) to lift
# coverage — plain themes are missing on ~35% of watchlist rows. The decade
# dimension is derived from release_year and handled separately.
_DIM_COLUMNS: dict[str, tuple[str, ...]] = {
    "directors": ("directors",),
    "genres": ("genres",),
    "themes": ("themes", "mini_themes"),
    "cast": ("cast",),
    "country": ("country",),
    "language": ("language",),
}

# Metadata the showtimes join strips (see data_loader.build_watchlist_showtimes
# _want_cols) that attach_match carries back along with the match score.
_CARRY_COLUMNS = ("themes", "mini_themes", "release_year", "cast", "country", "language")


@dataclasses.dataclass(frozen=True)
class TasteProfile:
    """Signed affinity profile derived from the user's ratings history.

    ``affinities`` maps dimension -> feature value -> shrunk centered affinity
    (positive = liked, negative = disliked). ``counts`` carries the raw n_v per
    value for formatting thresholds (e.g. directors need ≥2 rated films).
    """

    mu: float
    n_ratings: int
    affinities: dict[str, dict[str, float]]
    counts: dict[str, dict[str, int]]

    @property
    def is_empty(self) -> bool:
        return self.n_ratings == 0


_EMPTY_PROFILE = TasteProfile(mu=0.0, n_ratings=0, affinities={}, counts={})


def _film_features(row: pd.Series, dim: str) -> list[str]:
    """Extract a film's deduped feature values for one dimension.

    Comma-separated metadata cells are split and stripped; the decade dimension
    buckets ``release_year`` into a chip-friendly label (1994 -> ``"1990s"``).
    Missing/NaN cells yield an empty list so the dimension stays neutral.
    """
    if dim == "decade":
        year = row.get("release_year")
        if year is None or pd.isna(year):
            return []
        try:
            return [f"{int(year) // 10 * 10}s"]
        except (TypeError, ValueError):
            return []
    values: list[str] = []
    for col in _DIM_COLUMNS[dim]:
        cell = row.get(col)
        if isinstance(cell, str) and cell:
            values.extend(part.strip() for part in cell.split(",") if part.strip())
    return list(dict.fromkeys(values))


@st.cache_data(ttl=TTL_SECONDS)
def build_affinity(ratings_df: pd.DataFrame) -> TasteProfile:
    """Build the signed affinity profile from rated films.

    Rows with a null ``user_rating`` are skipped; feature values are deduped
    per film so a repeated genre cannot double-count. Returns an empty profile
    (``is_empty``) when there is no usable rating history.
    """
    if ratings_df.empty or "user_rating" not in ratings_df.columns:
        log.warning("Ratings DataFrame empty or missing user_rating — affinity profile unavailable")
        return _EMPTY_PROFILE
    rated = ratings_df.dropna(subset=["user_rating"])
    if rated.empty:
        return _EMPTY_PROFILE

    mu = float(rated["user_rating"].mean())
    dims = (*_DIM_COLUMNS, "decade")
    sums: dict[str, dict[str, float]] = {d: {} for d in dims}
    counts: dict[str, dict[str, int]] = {d: {} for d in dims}
    for _, row in rated.iterrows():
        deviation = float(row["user_rating"]) - mu
        for dim in dims:
            for value in _film_features(row, dim):
                sums[dim][value] = sums[dim].get(value, 0.0) + deviation
                counts[dim][value] = counts[dim].get(value, 0) + 1

    affinities = {
        dim: {value: total / (counts[dim][value] + SHRINKAGE_K) for value, total in dim_sums.items()}
        for dim, dim_sums in sums.items()
    }
    log.info(
        "Affinity profile built: %d ratings (mu=%.2f), %s",
        len(rated),
        mu,
        ", ".join(f"{d}={len(affinities[d])}" for d in dims),
    )
    return TasteProfile(mu=mu, n_ratings=len(rated), affinities=affinities, counts=counts)


def _raw_score(row: pd.Series, profile: TasteProfile) -> float:
    """Weighted blend of per-dimension affinity means plus the quality prior.

    Each dimension averages over the film's *known* values only — a film by an
    unrated director is neutral, and one loved genre is not diluted by an
    unknown sibling. A dimension with no known values contributes nothing.
    """
    raw = 0.0
    for dim, weight in WEIGHTS.items():
        dim_affinities = profile.affinities.get(dim, {})
        known = [dim_affinities[v] for v in _film_features(row, dim) if v in dim_affinities]
        if known:
            raw += weight * (sum(known) / len(known))
    lb_rating = row.get("letterboxd_avg_rating")
    if isinstance(lb_rating, (int, float)) and not pd.isna(lb_rating):
        raw += QUALITY_WEIGHT * (float(lb_rating) - QUALITY_CENTER)
    return raw


def score_films(df: pd.DataFrame, profile: TasteProfile) -> pd.Series:
    """Return a 0–100 match value per row, index-aligned with ``df``.

    A film with no known features and no Letterboxd rating lands at exactly 50
    (neutral). An empty profile degrades to the quality prior alone.
    """
    scores = [100.0 / (1.0 + math.exp(-_raw_score(row, profile) / LOGISTIC_TAU)) for _, row in df.iterrows()]
    return pd.Series(scores, index=df.index, dtype=float)


def explain(row: pd.Series, profile: TasteProfile, top_k: int = 2) -> list[tuple[str, float]]:
    """Top-k strictly positive contributions for "Because: …" chips.

    The contribution of value v in dimension d is ``WEIGHTS[d]·A_d(v)/m_d``
    where m_d is the film's number of known values in d — contributions within
    a dimension sum to its share of the raw score. Negative affinities are
    never surfaced ("because you hate X" is not a recommendation), and the
    quality prior is excluded (community taste, not the user's).
    """
    contributions: list[tuple[str, float]] = []
    for dim, weight in WEIGHTS.items():
        dim_affinities = profile.affinities.get(dim, {})
        known = [(v, dim_affinities[v]) for v in _film_features(row, dim) if v in dim_affinities]
        if not known:
            continue
        contributions.extend((value, weight * affinity / len(known)) for value, affinity in known)
    positive = sorted((c for c in contributions if c[1] > 0), key=lambda c: c[1], reverse=True)
    return positive[:top_k]


def attach_match(df: pd.DataFrame, watchlist_df: pd.DataFrame, profile: TasteProfile) -> pd.DataFrame:
    """Score the watchlist and left-join ``match`` onto ``df`` by ``tmdb_id``.

    Scoring happens on ``watchlist_df`` (full metadata) rather than ``df``
    because the showtimes join strips ``themes`` and ``release_year``; those
    columns ride along in the merge when absent from ``df`` so :func:`explain`
    works on the joined rows. Both key sides are cast to ``str`` (mirroring
    :func:`utils.data_loader.attach_streaming`); null-keyed watchlist rows are
    excluded so a stringified NaN can never produce a false match.

    Graceful no-op when ``df`` is empty or either side lacks ``tmdb_id``: the
    ``match`` column is present and all-NaN, so callers can ``dropna`` and
    render unchanged.
    """
    out = df.copy()
    if out.empty or "tmdb_id" not in out.columns or "tmdb_id" not in watchlist_df.columns:
        out["match"] = pd.Series(dtype=float)
        return out

    scored = watchlist_df.copy()
    scored["match"] = score_films(scored, profile)
    carry = ["match", *(c for c in _CARRY_COLUMNS if c in scored.columns and c not in out.columns)]
    keep = scored.loc[scored["tmdb_id"].notna(), ["tmdb_id", *carry]].copy()
    keep["tmdb_id"] = keep["tmdb_id"].astype(str)
    keep = keep.drop_duplicates(subset=["tmdb_id"])
    out["tmdb_id"] = out["tmdb_id"].astype(str)
    return out.merge(keep, on="tmdb_id", how="left")


def _top_values(affinities: dict[str, float], k: int) -> list[str]:
    """Top-k feature values by affinity descending (name breaks ties, stable output)."""
    return [v for v, _ in sorted(affinities.items(), key=lambda item: (-item[1], item[0]))[:k]]


def _bottom_negative(affinities: dict[str, float], k: int) -> list[str]:
    """Up to k strictly negative values, most disliked first."""
    negative = sorted(((v, a) for v, a in affinities.items() if a < 0), key=lambda item: (item[1], item[0]))
    return [v for v, _ in negative[:k]]


def format_taste_profile(profile: TasteProfile) -> str:
    """Render the profile as the compact summary string for the LLM prompt.

    Line prefixes ("Average rating given:", "Favourite genres:", "Favourite
    directors", the empty sentinel) are a stable contract with the chat evals
    and tests — extend with new lines rather than rewording existing ones.
    Dimensions with no qualifying values are omitted entirely.
    """
    if profile.is_empty:
        return "No rating history available."
    lines = [f"Average rating given: {profile.mu:.1f}/5 across {profile.n_ratings} films"]

    genres = profile.affinities.get("genres", {})
    if genres:
        lines.append(f"Favourite genres: {', '.join(_top_values(genres, 5))}")
        disliked_genres = _bottom_negative(genres, 3)
        if disliked_genres:
            lines.append(f"Least favourite genres: {', '.join(disliked_genres)}")

    themes = profile.affinities.get("themes", {})
    if themes:
        lines.append(f"Favourite themes: {', '.join(_top_values(themes, 5))}")

    directors = profile.affinities.get("directors", {})
    director_counts = profile.counts.get("directors", {})
    eligible = {v: a for v, a in directors.items() if director_counts.get(v, 0) >= 2}
    if eligible:
        lines.append(f"Favourite directors (≥2 films rated): {', '.join(_top_values(eligible, 5))}")
        disliked_directors = _bottom_negative(eligible, 3)
        if disliked_directors:
            lines.append(f"Least favourite directors (≥2 films rated): {', '.join(disliked_directors)}")

    actors = profile.affinities.get("cast", {})
    actor_counts = profile.counts.get("cast", {})
    # Positive-affinity guard: unlike directors, actors have no "Least favourite"
    # companion line, so a net-disliked actor's only path into the prompt would be
    # under the "Favourite" label — filter on sign, not just evidence count.
    eligible_actors = {v: a for v, a in actors.items() if actor_counts.get(v, 0) >= 2 and a > 0}
    if eligible_actors:
        lines.append(f"Favourite actors (≥2 films rated): {', '.join(_top_values(eligible_actors, 5))}")

    decades = profile.affinities.get("decade", {})
    if decades:
        lines.append(f"Favourite eras: {', '.join(_top_values(decades, 2))}")

    return "\n".join(lines)
