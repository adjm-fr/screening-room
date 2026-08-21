"""Tests for modules/match_calibration.py — the scorer calibration harness."""

import pandas as pd
from modules import matching
from modules.match_calibration import Metrics, Pair, SweepCandidate, build_labeled_pairs, evaluate, sweep


def _showtimes_df(rows: list[dict]) -> pd.DataFrame:
    base = {"movie": None, "original_title": None, "director": None, "release_year": None, "runtime": None}
    return pd.DataFrame([{**base, **r} for r in rows])


def _cache_df(rows: list[dict]) -> pd.DataFrame:
    base = {
        "slug": None,
        "title": None,
        "french_title": None,
        "original_title": None,
        "directors": None,
        "release_year": None,
        "runtime": None,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


# ── build_labeled_pairs ──────────────────────────────────────────────────────


def test_build_labeled_pairs_finds_a_tier1_positive():
    showtimes_df = _showtimes_df([{"movie": "RRR", "director": "S.S. Rajamouli", "release_year": 2022}])
    cache_df = _cache_df([{"slug": "rrr", "title": "RRR", "directors": "S. S. Rajamouli", "release_year": 2022}])

    positives, negatives = build_labeled_pairs(showtimes_df, cache_df)

    assert len(positives) == 1
    assert positives[0].candidate.slug == "rrr"
    assert positives[0].label is True
    assert negatives == []


def test_build_labeled_pairs_collects_same_title_collisions_as_negatives():
    showtimes_df = _showtimes_df([{"movie": "Le Retour", "director": "Andrey Zvyagintsev", "release_year": 2003}])
    cache_df = _cache_df(
        [
            {"slug": "the-return", "french_title": "Le Retour", "directors": "Andrey Zvyagintsev", "release_year": 2003},
            {"slug": "homecoming-2023", "french_title": "Le retour", "directors": "Catherine Corsini", "release_year": 2023},
        ]
    )

    positives, negatives = build_labeled_pairs(showtimes_df, cache_df)

    assert len(positives) == 1
    assert positives[0].candidate.slug == "the-return"
    assert len(negatives) == 1
    assert negatives[0].candidate.slug == "homecoming-2023"
    assert negatives[0].label is False


def test_build_labeled_pairs_skips_films_without_a_tier1_match():
    showtimes_df = _showtimes_df([{"movie": "Unknown Film", "director": "Nobody", "release_year": 2024}])
    cache_df = _cache_df([{"slug": "some-film", "title": "Something Else", "directors": "Someone", "release_year": 1990}])

    positives, negatives = build_labeled_pairs(showtimes_df, cache_df)

    assert positives == []
    assert negatives == []


def test_build_labeled_pairs_deduplicates_unique_films():
    showtimes_df = _showtimes_df(
        [
            {"movie": "RRR", "director": "S.S. Rajamouli", "release_year": 2022},
            {"movie": "RRR", "director": "S.S. Rajamouli", "release_year": 2022},
        ]
    )
    cache_df = _cache_df([{"slug": "rrr", "title": "RRR", "directors": "S. S. Rajamouli", "release_year": 2022}])

    positives, _ = build_labeled_pairs(showtimes_df, cache_df)

    assert len(positives) == 1


# ── evaluate ─────────────────────────────────────────────────────────────────


def _query(year=2022, runtime=None):
    return matching.Query(titles=("RRR",), year=year, director_tokens=(frozenset({"rajamouli"}),), runtime=runtime)


def _candidate(slug, year=2022, runtime=None):
    return matching.Candidate(slug=slug, titles=("RRR",), year=year, director_tokens=(frozenset({"rajamouli"}),), runtime=runtime)


def test_evaluate_counts_a_clean_positive_as_a_true_positive():
    q = _query()
    positives = [Pair(query=q, candidate=_candidate("rrr"), label=True)]
    metrics = evaluate(positives, [], weights=matching.WEIGHTS, accept=matching.ACCEPT, margin=matching.MARGIN)
    assert isinstance(metrics, Metrics)
    assert metrics.true_positives == 1
    assert metrics.wrong_picks == 0
    assert metrics.false_negatives == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


def test_evaluate_counts_a_confusable_collision_as_a_false_negative_not_a_wrong_pick():
    # Two candidates identical enough that MARGIN refuses to pick either —
    # the safe outcome (a false negative), never a silent wrong pick.
    q = _query()
    positives = [Pair(query=q, candidate=_candidate("correct"), label=True)]
    negatives = [Pair(query=q, candidate=_candidate("collision"), label=False)]
    metrics = evaluate(positives, negatives, weights=matching.WEIGHTS, accept=matching.ACCEPT, margin=matching.MARGIN)
    assert metrics.true_positives == 0
    assert metrics.false_negatives == 1
    assert metrics.wrong_picks == 0


def test_evaluate_reports_wrong_picks_with_examples():
    # A collision that outscores the correct candidate (e.g. year matches exactly on
    # the wrong slug) must show up as a wrong pick, with the title/slugs recorded.
    q = _query(year=2005)
    positives = [Pair(query=q, candidate=_candidate("correct", year=2010), label=True)]
    negatives = [Pair(query=q, candidate=_candidate("collision", year=2005), label=False)]
    metrics = evaluate(positives, negatives, weights=matching.WEIGHTS, accept=matching.ACCEPT, margin=0.0)
    assert metrics.wrong_picks == 1
    assert metrics.wrong_pick_examples[0] == ("RRR", "correct", "collision")
    assert metrics.precision == 0.0


# ── sweep ────────────────────────────────────────────────────────────────────


def test_sweep_returns_one_metrics_result_per_grid_point():
    q = _query()
    positives = [Pair(query=q, candidate=_candidate("rrr"), label=True)]
    grid = [
        SweepCandidate(weights=matching.WEIGHTS, accept=0.7, margin=0.05),
        SweepCandidate(weights=matching.WEIGHTS, accept=0.9, margin=0.05),
    ]
    results = sweep(positives, [], grid)
    assert len(results) == 2
    for candidate, metrics in results:
        assert isinstance(candidate, SweepCandidate)
        assert isinstance(metrics, Metrics)
