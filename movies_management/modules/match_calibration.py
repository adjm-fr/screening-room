"""
Calibration harness for :mod:`modules.matching`: measures precision/recall of
the combined scorer against ground truth derived from the real cache and
showtimes parquets, so ``WEIGHTS``/``ACCEPT``/``MARGIN``/``YEAR_TAPER``/
``RUNTIME_TAPER`` can be swept and compared against real numbers instead of
eyeballed once and left alone. Mirrors ``cinema_dashboard/core/backtest.py``'s
shape and rationale.

**Ground truth, derived from the real parquets, no hand-labelling.**

- **Positives**: the (film, cache row) pairs the *current* ``_match_cache``
  tier 1 resolves — exact ``release_year`` plus confirmed director-token
  overlap. Known-good: this is the resolution logic already shipping, not a
  guess.
- **Negatives**: for each positive, every *other* cache row sharing its
  normalised title (from either ``title`` or ``original_title``). In-distribution
  and free — no separate labelling pass needed. This set contains the ~36
  same-title/overlapping-director collisions documented in
  ``allocine_enrichment._match_by_runtime``'s docstring, including the 3 that
  are genuinely different films (``paranoia-1969``/``a-quiet-place-to-kill``,
  ``wild-and-woolfy``/``little-red-walking-hood``, ``who-killed-who``/
  ``thugs-with-dirty-mugs``) — a scorer that can't reject those isn't safe to
  ship.

**Why pairs are grouped by film before scoring, not scored independently.**
:func:`evaluate` calls :func:`modules.matching.best_match` once per film,
over that film's positive candidate *and* every collision sharing its title —
the same shape retrieval actually hands the scorer in production. Scoring a
positive and its negatives independently (one candidate at a time) would
never exercise :data:`modules.matching.MARGIN`, which only matters when two
or more candidates are on the table together; grouping is what makes the
"same-title collision" ground truth meaningful at all.

A film counts as a **true positive** when ``best_match`` returns the correct
slug, a **false negative** when it returns nothing, and a **false positive**
(a *wrong pick*, the dangerous outcome) when it confidently returns the wrong
slug from among that film's collisions.

Public API:
    build_labeled_pairs(showtimes_df, cache_df) -> (positives, negatives)
    evaluate(positives, negatives, *, weights, accept, margin) -> Metrics
    sweep(positives, negatives, grid) -> list[(SweepCandidate, Metrics)]
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from modules import allocine_enrichment as ae
from modules import matching


@dataclass(frozen=True)
class Pair:
    """One (query, candidate) ground-truth pair. ``label`` is True for the correct match."""

    query: matching.Query
    candidate: matching.Candidate
    label: bool


@dataclass(frozen=True)
class Metrics:
    """Precision/recall over the grouped-by-film evaluation. See module docstring."""

    precision: float
    recall: float
    true_positives: int
    wrong_picks: int
    false_negatives: int
    total_positives: int
    wrong_pick_examples: tuple[tuple[str, str, str], ...]  # (title, correct_slug, picked_slug)


@dataclass(frozen=True)
class SweepCandidate:
    """One point in a :func:`sweep` grid."""

    weights: dict[str, float]
    accept: float
    margin: float


def build_labeled_pairs(showtimes_df: pd.DataFrame, cache_df: pd.DataFrame) -> tuple[list[Pair], list[Pair]]:
    """Derive (positives, negatives) ground truth from the real showtimes + cache parquets.

    See the module docstring for what counts as a positive/negative. Films
    with no parseable ``release_year`` or no director tokens are skipped —
    the same precision-first rejection :func:`allocine_enrichment._match_cache`
    already applies, so this never invents a positive the shipping code
    wouldn't have found.
    """
    cache_index = ae._build_cache_index(cache_df)

    key_cols = [c for c in ("movie", "original_title", "director", "release_year", "runtime") if c in showtimes_df.columns]
    unique_films = showtimes_df[key_cols].drop_duplicates().reset_index(drop=True)

    positives: list[Pair] = []
    negatives: list[Pair] = []

    for _, row in unique_films.iterrows():
        title = str(row.get("movie") or "").strip()
        if not title:
            continue
        original_title = str(row.get("original_title") or "").strip() or None
        director = str(row.get("director") or "").strip() or None
        film = {
            "title": title,
            "original_title": original_title,
            "director": director,
            "release_year": row.get("release_year"),
            "runtime": row.get("runtime"),
        }

        try:
            year = int(film["release_year"])
        except (TypeError, ValueError):
            continue
        allocine_tokens = ae._split_director_tokens(film["director"], "|")
        if not allocine_tokens:
            continue

        # Ground truth is the OLD two-tier definition: title-blocked, director-
        # confirmed, exact release_year, first match in title-then-original_title
        # order. Reimplemented here rather than imported, because the shipping code
        # no longer has those tiers — this harness is precisely what proves the
        # scorer reproduces them, so the definition must be pinned independently of
        # whatever allocine_enrichment does now.
        confirmed: dict[str, matching.Candidate] = {}
        for t in (title, original_title):
            norm = ae._normalize_title(t)
            if not norm:
                continue
            for cand in cache_index.get(norm, []):
                if cand.slug in confirmed:
                    continue
                if ae._directors_overlap(list(allocine_tokens), list(cand.director_tokens)):
                    confirmed[cand.slug] = cand
        tier1 = [c for c in confirmed.values() if c.year == year]
        if not tier1:
            continue
        match = tier1[0]

        query = matching.Query(
            titles=tuple(t for t in (title, original_title) if t),
            year=year,
            director_tokens=tuple(allocine_tokens),
            runtime=ae._parse_runtime(film["runtime"]),
        )
        positives.append(Pair(query=query, candidate=match, label=True))

        seen_slugs = {match.slug}
        for t in (title, original_title):
            norm = ae._normalize_title(t)
            if not norm:
                continue
            for cand in cache_index.get(norm, []):
                if cand.slug in seen_slugs:
                    continue
                seen_slugs.add(cand.slug)
                negatives.append(Pair(query=query, candidate=cand, label=False))

    return positives, negatives


@dataclass
class _Group:
    """Mutable accumulator: one film's positive candidate plus every candidate on the table for it."""

    positive: matching.Candidate | None = None
    candidates: list[matching.Candidate] = field(default_factory=list)


def evaluate(
    positives: list[Pair],
    negatives: list[Pair],
    *,
    weights: dict[str, float],
    accept: float,
    margin: float,
) -> Metrics:
    """Group positives/negatives by film and score each film's whole candidate set at once.

    See the module docstring for why grouping (not independent per-pair
    scoring) is what actually exercises ``margin``.
    """
    groups: dict[matching.Query, _Group] = {}
    for pair in positives:
        g = groups.setdefault(pair.query, _Group())
        g.positive = pair.candidate
        g.candidates.append(pair.candidate)
    for pair in negatives:
        g = groups.setdefault(pair.query, _Group())
        g.candidates.append(pair.candidate)

    true_positives = 0
    wrong_picks = 0
    false_negatives = 0
    wrong_pick_examples: list[tuple[str, str, str]] = []

    for query, g in groups.items():
        positive = g.positive
        if positive is None:
            continue
        result = matching.best_match(query, g.candidates, weights=weights, accept=accept, margin=margin)
        if result is not None and result[0].slug == positive.slug:
            true_positives += 1
        elif result is not None:
            wrong_picks += 1
            title = query.titles[0] if query.titles else "?"
            wrong_pick_examples.append((title, positive.slug, result[0].slug))
        else:
            false_negatives += 1

    total_positives = true_positives + wrong_picks + false_negatives
    precision = true_positives / (true_positives + wrong_picks) if (true_positives + wrong_picks) else 1.0
    recall = true_positives / total_positives if total_positives else 0.0

    return Metrics(
        precision=precision,
        recall=recall,
        true_positives=true_positives,
        wrong_picks=wrong_picks,
        false_negatives=false_negatives,
        total_positives=total_positives,
        wrong_pick_examples=tuple(wrong_pick_examples),
    )


def sweep(positives: list[Pair], negatives: list[Pair], grid: list[SweepCandidate]) -> list[tuple[SweepCandidate, Metrics]]:
    """Evaluate every point in ``grid`` against the same ground truth."""
    return [
        (candidate, evaluate(positives, negatives, weights=candidate.weights, accept=candidate.accept, margin=candidate.margin))
        for candidate in grid
    ]
