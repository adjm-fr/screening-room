"""Unit tests for utils.taste — affinity math, scorer, explanations, merge, formatter."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from utils.taste import (
    LOGISTIC_TAU,
    QUALITY_WEIGHT,
    TasteProfile,
    attach_match,
    build_affinity,
    explain,
    format_taste_profile,
    score_films,
)


def _logistic(raw: float) -> float:
    return 100.0 / (1.0 + math.exp(-raw / LOGISTIC_TAU))


def _profile(affinities: dict[str, dict[str, float]], n_ratings: int = 10) -> TasteProfile:
    """Hand-built profile for scorer/explain tests (counts unused by both)."""
    return TasteProfile(mu=3.0, n_ratings=n_ratings, affinities=affinities, counts={})


# ---------------------------------------------------------------------------
# build_affinity
# ---------------------------------------------------------------------------


def test_affinity_centering_and_sign(make_ratings):
    df = make_ratings([{"user_rating": 5.0, "genres": "A"}, {"user_rating": 1.0, "genres": "B"}])
    profile = build_affinity(df)
    assert profile.mu == pytest.approx(3.0)
    assert profile.affinities["genres"]["A"] == pytest.approx((5.0 - 3.0) / (1 + 5))
    assert profile.affinities["genres"]["B"] == pytest.approx(-(2.0) / 6)


def test_affinity_shrinkage_exact(make_ratings):
    rows = [{"user_rating": 5.0, "directors": "Multi"} for _ in range(3)]
    rows += [{"user_rating": 5.0, "directors": "Solo"}, {"user_rating": 1.0, "directors": "Other"}]
    profile = build_affinity(make_ratings(rows))
    # mu = 4.2; same mean rating, but 3 films of evidence beat 1 under shrinkage
    assert profile.affinities["directors"]["Multi"] == pytest.approx(0.8 * 3 / (3 + 5))
    assert profile.affinities["directors"]["Solo"] == pytest.approx(0.8 / (1 + 5))
    assert profile.affinities["directors"]["Multi"] > profile.affinities["directors"]["Solo"]


def test_affinity_mini_themes_folded_into_themes(make_ratings):
    df = make_ratings(
        [
            {"user_rating": 5.0, "themes": "Heist", "mini_themes": "Noir"},
            {"user_rating": 1.0, "themes": "War"},
        ]
    )
    themes = build_affinity(df).affinities["themes"]
    assert themes["Heist"] == pytest.approx(1 / 3)
    assert themes["Noir"] == pytest.approx(1 / 3)
    assert themes["War"] == pytest.approx(-1 / 3)


def test_affinity_dedupes_repeated_value_within_film(make_ratings):
    df = make_ratings([{"user_rating": 5.0, "genres": "Drama, Drama"}, {"user_rating": 1.0, "genres": "Comedy"}])
    profile = build_affinity(df)
    assert profile.counts["genres"]["Drama"] == 1
    assert profile.affinities["genres"]["Drama"] == pytest.approx(2.0 / 6)


def test_affinity_decade_bucket_label(make_ratings):
    df = make_ratings([{"user_rating": 5.0, "release_year": 1994}, {"user_rating": 1.0, "release_year": 2006}])
    decades = build_affinity(df).affinities["decade"]
    assert set(decades) == {"1990s", "2000s"}


def test_affinity_empty_and_missing_user_rating():
    assert build_affinity(pd.DataFrame()).is_empty
    assert build_affinity(pd.DataFrame({"genres": ["Drama"]})).is_empty
    assert build_affinity(pd.DataFrame()).affinities == {}


def test_affinity_skips_nan_user_rating_rows(make_ratings):
    df = make_ratings([{"user_rating": 5.0, "genres": "A"}, {"user_rating": None, "genres": "B"}])
    profile = build_affinity(df)
    assert profile.n_ratings == 1
    assert "B" not in profile.counts["genres"]


# ---------------------------------------------------------------------------
# score_films
# ---------------------------------------------------------------------------


def test_score_unknown_features_neutral_is_50(make_ratings):
    profile = build_affinity(make_ratings([{"user_rating": 5.0, "genres": "A"}, {"user_rating": 1.0, "genres": "B"}]))
    df = pd.DataFrame([{"genres": "Zzz", "directors": "Nobody", "release_year": 1875}])
    assert score_films(df, profile).iloc[0] == pytest.approx(50.0)


def test_score_mean_over_known_only():
    profile = _profile({"genres": {"A": 0.5}})
    df = pd.DataFrame([{"genres": "A"}, {"genres": "A, Zzz"}])
    scores = score_films(df, profile)
    assert scores.iloc[0] == pytest.approx(scores.iloc[1])


def test_score_dimension_weights_ordering():
    profile = _profile({"directors": {"D": 0.5}, "decade": {"2000s": 0.5}})
    df = pd.DataFrame([{"directors": "D"}, {"release_year": 2005}])
    scores = score_films(df, profile)
    assert scores.iloc[0] == pytest.approx(_logistic(1.0 * 0.5))
    assert scores.iloc[1] == pytest.approx(_logistic(0.3 * 0.5))
    assert scores.iloc[0] > scores.iloc[1]


def test_score_quality_prior_sign_and_nan():
    profile = _profile({})
    df = pd.DataFrame({"letterboxd_avg_rating": [4.5, float("nan"), 2.5]})
    scores = score_films(df, profile)
    assert scores.iloc[0] == pytest.approx(_logistic(QUALITY_WEIGHT * 1.0))
    assert scores.iloc[1] == pytest.approx(50.0)
    assert scores.iloc[2] == pytest.approx(_logistic(QUALITY_WEIGHT * -1.0))


def test_score_missing_metadata_columns_no_error():
    profile = _profile({"directors": {"D": 0.5}})
    df = pd.DataFrame({"title": ["X"]})
    assert score_films(df, profile).iloc[0] == pytest.approx(50.0)


def test_score_bounds_and_index_alignment():
    profile = _profile({"genres": {"A": 2.0, "B": -2.0}})
    df = pd.DataFrame({"genres": ["A", "B"]}, index=[10, 20])
    scores = score_films(df, profile)
    assert scores.index.equals(df.index)
    assert ((scores > 0) & (scores < 100)).all()


def test_score_empty_profile_quality_only():
    profile = build_affinity(pd.DataFrame())
    df = pd.DataFrame([{"letterboxd_avg_rating": 4.5, "genres": "A"}, {"genres": "A"}])
    scores = score_films(df, profile)
    assert scores.iloc[0] == pytest.approx(_logistic(QUALITY_WEIGHT * 1.0))
    assert scores.iloc[1] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


def test_explain_topk_sorted_positive_only():
    profile = _profile({"directors": {"Great": 0.9}, "genres": {"Good": 0.5, "Bad": -0.5}})
    row = pd.Series({"directors": "Great", "genres": "Good, Bad"})
    top2 = explain(row, profile, top_k=2)
    assert [label for label, _ in top2] == ["Great", "Good"]
    assert explain(row, profile, top_k=1) == [top2[0]]
    assert all(contribution > 0 for _, contribution in top2)


def test_explain_contribution_formula_exact():
    profile = _profile({"directors": {"Great": 0.9}, "genres": {"Good": 0.5, "Bad": -0.5}})
    row = pd.Series({"directors": "Great", "genres": "Good, Bad"})
    contributions = dict(explain(row, profile, top_k=2))
    assert contributions["Great"] == pytest.approx(1.0 * 0.9 / 1)
    assert contributions["Good"] == pytest.approx(0.6 * 0.5 / 2)


def test_explain_all_negative_returns_empty():
    profile = _profile({"directors": {"Meh": -0.3}, "genres": {"Bad": -0.5}})
    row = pd.Series({"directors": "Meh", "genres": "Bad"})
    assert explain(row, profile) == []


# ---------------------------------------------------------------------------
# attach_match
# ---------------------------------------------------------------------------


def test_attach_match_merges_by_tmdb_id_str_cast():
    profile = _profile({"genres": {"A": 0.5}})
    watchlist = pd.DataFrame([{"tmdb_id": 1, "genres": "A"}, {"tmdb_id": 2, "genres": "Zzz"}])
    df = pd.DataFrame([{"tmdb_id": "1", "movie": "Film"}])
    out = attach_match(df, watchlist, profile)
    assert out["match"].iloc[0] == pytest.approx(_logistic(0.6 * 0.5))


def test_attach_match_carries_themes_and_release_year():
    profile = _profile({})
    watchlist = pd.DataFrame([{"tmdb_id": 1, "genres": "A", "themes": "Heist", "mini_themes": "Noir", "release_year": 1994}])
    out = attach_match(pd.DataFrame([{"tmdb_id": 1, "movie": "X"}]), watchlist, profile)
    assert out["themes"].iloc[0] == "Heist"
    assert out["mini_themes"].iloc[0] == "Noir"
    assert out["release_year"].iloc[0] == 1994


def test_attach_match_graceful_without_tmdb_id():
    profile = _profile({})
    watchlist = pd.DataFrame([{"tmdb_id": 1, "genres": "A"}])
    out = attach_match(pd.DataFrame([{"movie": "X"}]), watchlist, profile)
    assert "match" in out.columns
    assert out["match"].isna().all()
    # NaN keys must not string-match each other ("nan" == "nan" would be a false positive)
    nan_df = pd.DataFrame([{"tmdb_id": float("nan"), "movie": "Y"}])
    nan_watchlist = pd.DataFrame([{"tmdb_id": float("nan"), "genres": "A"}])
    assert attach_match(nan_df, nan_watchlist, profile)["match"].isna().all()


# ---------------------------------------------------------------------------
# format_taste_profile
# ---------------------------------------------------------------------------


def test_format_lines_present_and_dislikes(make_ratings):
    df = make_ratings(
        [
            {"user_rating": 5.0, "genres": "Western", "directors": "Howard Hawks", "release_year": 1950},
            {"user_rating": 4.5, "genres": "Western", "directors": "Howard Hawks", "release_year": 1959},
            {"user_rating": 1.0, "genres": "Comedy", "directors": "Dany Boon", "release_year": 2008},
            {"user_rating": 0.5, "genres": "Comedy", "directors": "Dany Boon", "release_year": 2010},
        ]
    )
    result = format_taste_profile(build_affinity(df))
    assert result.startswith("Average rating given: 2.8/5 across 4 films")
    assert "Favourite genres: Western" in result
    assert "Least favourite genres: Comedy" in result
    assert "Favourite directors (≥2 films rated): Howard Hawks" in result
    assert "Least favourite directors (≥2 films rated): Dany Boon" in result
    assert "Favourite eras: 1950s" in result


def test_format_omits_empty_dimensions():
    result = format_taste_profile(build_affinity(pd.DataFrame({"user_rating": [3.0, 5.0]})))
    assert result.startswith("Average rating given: 4.0")
    assert "genres" not in result
    assert "directors" not in result
    assert "themes" not in result
    assert "eras" not in result


def test_format_empty_profile_sentinel():
    assert format_taste_profile(build_affinity(pd.DataFrame())) == "No rating history available."
