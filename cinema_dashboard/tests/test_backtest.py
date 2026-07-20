"""Unit tests for utils.backtest — holdout splitting, raw scoring, and evaluate()."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from utils.backtest import evaluate, random_holdout_splits, raw_scores
from utils.taste import QUALITY_CENTER, TasteProfile

# ---------------------------------------------------------------------------
# random_holdout_splits
# ---------------------------------------------------------------------------


def test_random_holdout_splits_same_seed_identical(make_ratings):
    df = make_ratings([{"user_rating": float(i % 5 + 1)} for i in range(50)])
    a = random_holdout_splits(df, holdout_frac=0.2, n_repeats=5, seed=7)
    b = random_holdout_splits(df, holdout_frac=0.2, n_repeats=5, seed=7)
    for (train_a, test_a), (train_b, test_b) in zip(a, b, strict=True):
        assert train_a["user_rating"].tolist() == train_b["user_rating"].tolist()
        assert test_a["user_rating"].tolist() == test_b["user_rating"].tolist()


def test_random_holdout_splits_different_seeds_differ(make_ratings):
    df = make_ratings([{"user_rating": float(i % 5 + 1)} for i in range(50)])
    a = random_holdout_splits(df, n_repeats=3, seed=1)
    b = random_holdout_splits(df, n_repeats=3, seed=2)
    assert any(ta["user_rating"].tolist() != tb["user_rating"].tolist() for (ta, _), (tb, _) in zip(a, b, strict=True))


def test_random_holdout_splits_counts_and_sizes(make_ratings):
    df = make_ratings([{"user_rating": 3.0} for _ in range(100)])
    splits = random_holdout_splits(df, holdout_frac=0.25, n_repeats=10, seed=3)
    assert len(splits) == 10
    for train_df, test_df in splits:
        assert len(test_df) == 25
        assert len(train_df) == 75


def test_random_holdout_splits_disjoint_and_excludes_nulls(make_ratings):
    rows = [{"user_rating": 3.0} for _ in range(20)] + [{"user_rating": None} for _ in range(5)]
    df = make_ratings(rows)
    train_df, test_df = random_holdout_splits(df, holdout_frac=0.2, n_repeats=1, seed=4)[0]
    assert len(train_df) + len(test_df) == 20
    assert train_df["user_rating"].notna().all()
    assert test_df["user_rating"].notna().all()
    assert set(train_df.index).isdisjoint(set(test_df.index))


# ---------------------------------------------------------------------------
# raw_scores
# ---------------------------------------------------------------------------


def test_raw_scores_matches_taste_raw_score_per_row():
    profile = TasteProfile(mu=3.0, n_ratings=10, affinities={"genres": {"A": 0.5}}, counts={"genres": {"A": 10}})
    df = pd.DataFrame([{"genres": "A"}, {"genres": "Zzz"}], index=[5, 9])
    scores = raw_scores(df, profile, weights={"genres": 0.6}, quality_weight=0.2)
    assert scores.index.equals(df.index)
    assert scores.loc[5] == pytest.approx(0.6 * 0.5)
    assert scores.loc[9] == pytest.approx(0.0)


def test_raw_scores_empty_weights_quality_only():
    # weights={} makes taste._raw_score's `for dim, weight in weights.items()` loop
    # a no-op — only the quality prior term should survive, and it must not crash.
    profile = TasteProfile(mu=3.0, n_ratings=10, affinities={"genres": {"A": 0.9}}, counts={"genres": {"A": 10}})
    df = pd.DataFrame([{"genres": "A", "letterboxd_avg_rating": 4.5}])
    scores = raw_scores(df, profile, weights={}, quality_weight=0.2)
    assert scores.iloc[0] == pytest.approx(0.2 * (4.5 - QUALITY_CENTER))


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_monotone_case_spearman_near_one(make_ratings):
    # One distinct genre per rating tier, many repetitions per tier: the
    # affinity of G_i is (tier_rating - mu)*n_i/(n_i+k), strictly increasing
    # in tier_rating since n_i is balanced across tiers — so scoring on
    # "genres" alone should recover an (almost) perfect rank correlation.
    tiers = [1.0, 2.0, 3.0, 4.0, 5.0]
    rows = [{"user_rating": rating, "genres": f"G{i}"} for i, rating in enumerate(tiers, start=1) for _ in range(30)]
    df = make_ratings(rows)

    result = evaluate(
        df,
        shrinkage_k=5.0,
        weights={"genres": 1.0},
        quality_weight=0.0,
        holdout_frac=0.2,
        n_repeats=5,
        seed=1,
    )
    assert result["spearman"] == pytest.approx(1.0, abs=0.05)
    assert result["quartile_lift"] > 0
    # quality_weight=0.0 and weights={} make the baseline a constant score
    # column (zero rank variance) — correlation is undefined, not a crash.
    assert math.isnan(result["baseline_spearman"])


def test_evaluate_baseline_uses_quality_weight_only(make_ratings):
    tiers = [1.0, 2.0, 3.0, 4.0, 5.0]
    rows = [
        {"user_rating": rating, "letterboxd_avg_rating": rating, "genres": f"G{i}"}
        for i, rating in enumerate(tiers, start=1)
        for _ in range(10)
    ]
    df = make_ratings(rows)

    result = evaluate(
        df,
        shrinkage_k=5.0,
        weights={"genres": 1.0},
        quality_weight=0.3,
        holdout_frac=0.2,
        n_repeats=5,
        seed=2,
    )
    assert set(result) == {"spearman", "quartile_lift", "baseline_spearman", "baseline_quartile_lift"}
    # Baseline drops the genres dimension but keeps the quality prior, and
    # letterboxd_avg_rating tracks user_rating exactly here, so it should
    # still correlate strongly rather than crash or come back degenerate.
    assert result["baseline_spearman"] > 0.5


def test_evaluate_all_repeats_skipped_raises(make_ratings):
    # 3 rows total is below _MIN_TEST_ROWS_FOR_QUARTILES for any holdout_frac
    # that keeps at least one train row, so every repeat must be skipped.
    df = make_ratings([{"user_rating": 3.0}, {"user_rating": 4.0}, {"user_rating": 2.0}])
    with pytest.raises(ValueError, match="No repeat"):
        evaluate(
            df,
            shrinkage_k=5.0,
            weights={"genres": 1.0},
            quality_weight=0.0,
            holdout_frac=0.2,
            n_repeats=3,
            seed=1,
        )
