"""
Backtest harness: measure how well the taste ranker's raw score predicts a
held-out rating, so the provisional constants in :mod:`utils.taste`
(``SHRINKAGE_K``, ``WEIGHTS``, ``QUALITY_WEIGHT``) can be swept and compared
against real numbers instead of eyeballed once and left alone.

**Why repeated random holdout, not a temporal split.** The ratings history
carries no watch-date column (see ``packages/contracts``/the parquet schema),
so there is no way to hold out "the last N months" the way a real
recommender backtest normally would. Instead :func:`random_holdout_splits`
draws several independent random holdouts from a single seeded generator —
repeats average out the variance any one random split would carry, and the
seed makes every run reproducible (same seed -> byte-identical splits, so a
weight sweep compares candidates on the *same* train/test partitions rather
than confounding the weight change with a different random split).

**Why raw scores, not the 0-100 match value.** :func:`utils.taste.score_films`
maps the raw blend through a fixed logistic (``100 / (1 + exp(-raw / tau))``)
purely for display scaling. The logistic is strictly monotone increasing, so
it cannot change the *rank order* of scored films — and both metrics this
module computes (Spearman rank correlation, and a quartile split by score)
are rank-based. Skipping the logistic is therefore a pure optimization: same
answer, fewer float ops, and no dependency on ``LOGISTIC_TAU``.

**Why a quantile-based quartile split, not nlargest/nsmallest.** Ratings are
quantized to half-star steps, so many rows tie on ``user_rating`` and,
depending on the swept weights, on raw score too. ``nlargest``/``nsmallest``
break ties by row order, which would let the current DataFrame's row order
quietly bias which tied rows land in the "top" vs. the rest. Quantile-based
masks treat every tied row identically (all-in or all-out together), so the
lift metric depends only on the score distribution, not on incidental frame
ordering.

Public API:
    random_holdout_splits(ratings_df, ...)  -> list of (train_df, test_df)
    raw_scores(df, profile, weights=..., quality_weight=...)  -> pre-logistic scores
    evaluate(ratings_df, shrinkage_k=..., weights=..., quality_weight=...)  -> metric dict
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils import taste

# Below this many held-out rows, a quartile split is too small to be a
# meaningful signal (e.g. 3 test rows -> a "top 25%" of one row is really
# "top 1"). Repeats that land under this are skipped rather than crashing or
# silently mixing in a degenerate metric.
_MIN_TEST_ROWS_FOR_QUARTILES = 4


def random_holdout_splits(
    ratings_df: pd.DataFrame,
    holdout_frac: float = 0.2,
    n_repeats: int = 20,
    seed: int = 42,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Seeded repeated random holdout over rows with a non-null ``user_rating``.

    There is no watch-date column on the ratings history, so a temporal
    "held out the most recent N%" split — the usual choice for a
    recommender backtest — isn't possible here; this draws ``n_repeats``
    independent random holdouts instead, each keeping ``holdout_frac`` of the
    rated rows for test. A single ``numpy.random.default_rng(seed)`` drives
    every repeat's shuffle, so the same ``seed`` always reproduces identical
    splits (across calls and processes) — deliberately not using pandas'
    ``DataFrame.sample`` with a fresh/global RNG, which would not give that
    guarantee.
    """
    rated = ratings_df.dropna(subset=["user_rating"]).reset_index(drop=True)
    n = len(rated)
    n_test = max(1, round(n * holdout_frac)) if n else 0
    rng = np.random.default_rng(seed)

    splits: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    for _ in range(n_repeats):
        perm = rng.permutation(n)
        test_idx = perm[:n_test]
        train_idx = perm[n_test:]
        splits.append((rated.iloc[train_idx].copy(), rated.iloc[test_idx].copy()))
    return splits


def raw_scores(
    df: pd.DataFrame,
    profile: taste.TasteProfile,
    *,
    weights: dict[str, float],
    quality_weight: float,
) -> pd.Series:
    """Pre-logistic raw score per row, index-aligned with ``df``.

    Mirrors :func:`utils.taste.score_films`'s ``iterrows`` loop but stops
    short of the logistic — see the module docstring for why that's a safe,
    metric-preserving shortcut for the rank-based evaluation in
    :func:`evaluate`.
    """
    scores = [taste._raw_score(row, profile, weights=weights, quality_weight=quality_weight) for _, row in df.iterrows()]
    return pd.Series(scores, index=df.index, dtype=float)


def _spearman_and_lift(scores: pd.Series, truth: pd.Series) -> tuple[float, float]:
    """Spearman correlation and top-vs-bottom-quartile mean-rating lift for one split.

    Spearman's rho is Pearson's r computed on the rank-transformed values;
    that identity is used directly (``.rank().corr()``) rather than
    ``Series.corr(method="spearman")`` because pandas' spearman/kendall paths
    import ``scipy.stats`` under the hood (``pandas.core.nanops.get_corr_func``)
    while its default ``pearson`` path uses ``numpy.corrcoef`` directly — and
    this project has no scipy dependency to spend on it. ``.rank()`` averages
    tied values by default, matching how scipy's ``spearmanr`` handles the
    half-star ties in ``user_rating``. A constant score column (e.g. the
    quality-only baseline with ``quality_weight=0``) has zero rank variance,
    for which correlation is mathematically undefined; that's checked for
    explicitly rather than left to ``numpy.corrcoef``, which would raise a
    divide-by-zero ``RuntimeWarning`` (a hard failure under this repo's
    ``filterwarnings = ["error"]``) instead of returning ``nan``.
    """
    score_ranks, truth_ranks = scores.rank(), truth.rank()
    if score_ranks.nunique() <= 1 or truth_ranks.nunique() <= 1:
        spearman = float("nan")
    else:
        spearman = float(score_ranks.corr(truth_ranks))
    low, high = scores.quantile(0.25), scores.quantile(0.75)
    top = truth[scores >= high]
    bottom = truth[scores <= low]
    lift = float(top.mean() - bottom.mean())
    return spearman, lift


def evaluate(
    ratings_df: pd.DataFrame,
    *,
    shrinkage_k: float,
    weights: dict[str, float],
    quality_weight: float,
    holdout_frac: float = 0.2,
    n_repeats: int = 20,
    seed: int = 42,
) -> dict[str, float]:
    """Mean out-of-sample Spearman correlation and quartile lift over repeated holdouts.

    For each split, an affinity profile is trained on ``train_df`` alone
    (with the candidate ``shrinkage_k``) and scored against the untouched
    ``test_df`` (with the candidate ``weights``/``quality_weight``) — so the
    numbers reflect genuine held-out predictive power, not in-sample fit. A
    quality-prior-only baseline (``weights={}``, so only the
    ``quality_weight`` term survives in :func:`utils.taste._raw_score`'s
    ``for dim, weight in weights.items()`` loop — an empty dict makes that
    loop a no-op, contributing nothing) is computed alongside on the same
    splits, so the taste dimensions' lift over "just trust Letterboxd's
    community rating" is directly comparable. Splits with too few test rows
    for a meaningful quartile split are skipped (see
    ``_MIN_TEST_ROWS_FOR_QUARTILES``); a ``ValueError`` is raised only if
    every repeat is skipped.
    """
    splits = random_holdout_splits(ratings_df, holdout_frac=holdout_frac, n_repeats=n_repeats, seed=seed)

    spearmans: list[float] = []
    lifts: list[float] = []
    baseline_spearmans: list[float] = []
    baseline_lifts: list[float] = []

    for train_df, test_df in splits:
        if len(test_df) < _MIN_TEST_ROWS_FOR_QUARTILES:
            continue
        profile = taste.build_affinity(train_df, shrinkage_k=shrinkage_k)
        truth = test_df["user_rating"]

        scores = raw_scores(test_df, profile, weights=weights, quality_weight=quality_weight)
        spearman, lift = _spearman_and_lift(scores, truth)
        spearmans.append(spearman)
        lifts.append(lift)

        baseline_scores = raw_scores(test_df, profile, weights={}, quality_weight=quality_weight)
        baseline_spearman, baseline_lift = _spearman_and_lift(baseline_scores, truth)
        baseline_spearmans.append(baseline_spearman)
        baseline_lifts.append(baseline_lift)

    if not spearmans:
        raise ValueError(
            f"No repeat had >= {_MIN_TEST_ROWS_FOR_QUARTILES} test rows "
            f"(n_repeats={n_repeats}, holdout_frac={holdout_frac}) — cannot evaluate."
        )

    return {
        "spearman": float(np.mean(spearmans)),
        "quartile_lift": float(np.mean(lifts)),
        "baseline_spearman": float(np.mean(baseline_spearmans)),
        "baseline_quartile_lift": float(np.mean(baseline_lifts)),
    }
