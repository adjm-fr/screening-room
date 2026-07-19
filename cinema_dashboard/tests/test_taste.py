"""Unit tests for utils.taste — affinity math, scorer, explanations, merge, formatter."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from utils.taste import (
    LOGISTIC_TAU,
    QUALITY_WEIGHT,
    TasteProfile,
    _mean_rating,
    attach_match,
    build_affinity,
    explain,
    format_taste_profile,
    score_films,
)


def _logistic(raw: float) -> float:
    return 100.0 / (1.0 + math.exp(-raw / LOGISTIC_TAU))


def _profile(affinities: dict[str, dict[str, float]], n_ratings: int = 10) -> TasteProfile:
    """Hand-built profile for scorer/explain tests; counts mirror every value (n=10, so mean rating = 3 + 1.5·a)."""
    counts = {dim: {value: 10 for value in values} for dim, values in affinities.items()}
    return TasteProfile(mu=3.0, n_ratings=n_ratings, affinities=affinities, counts=counts)


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


def test_affinity_cast_shrinkage_exact(make_ratings):
    rows = [{"user_rating": 5.0, "cast": "Recurring Lead"} for _ in range(3)]
    rows += [{"user_rating": 5.0, "cast": "One Timer"}, {"user_rating": 1.0, "cast": "Other"}]
    profile = build_affinity(make_ratings(rows))
    # mu = 4.2; same mean rating, but 3 films of evidence beat 1 under shrinkage
    assert profile.affinities["cast"]["Recurring Lead"] == pytest.approx(0.8 * 3 / (3 + 5))
    assert profile.affinities["cast"]["One Timer"] == pytest.approx(0.8 / (1 + 5))
    assert profile.affinities["cast"]["Recurring Lead"] > profile.affinities["cast"]["One Timer"]


def test_affinity_country_and_language_dimensions(make_ratings):
    df = make_ratings(
        [
            {"user_rating": 5.0, "country": "France", "language": "French"},
            {"user_rating": 1.0, "country": "USA", "language": "English"},
        ]
    )
    profile = build_affinity(df)
    assert profile.affinities["country"]["France"] == pytest.approx(2.0 / 6)
    assert profile.affinities["country"]["USA"] == pytest.approx(-2.0 / 6)
    assert profile.affinities["language"]["French"] == pytest.approx(2.0 / 6)
    assert profile.affinities["language"]["English"] == pytest.approx(-2.0 / 6)


def test_affinity_missing_cast_column_neutral(make_ratings):
    # Pre-backfill history: no cast column at all, and NaN cells — both must be neutral, never a crash.
    profile = build_affinity(make_ratings([{"user_rating": 5.0}, {"user_rating": 1.0}]))
    assert profile.affinities["cast"] == {}
    nan_df = pd.DataFrame([{"user_rating": 5.0, "cast": float("nan"), "genres": "A"}])
    assert build_affinity(nan_df).affinities["cast"] == {}


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


def test_mean_rating_inverts_shrunk_affinity(make_ratings):
    # Sentiment classification back-solves the raw mean from A = Σ(r−μ)/(n+k);
    # this pins the inversion to build_affinity's exact formula.
    df = make_ratings(
        [
            {"user_rating": 2.5, "directors": "Mid Band"},
            {"user_rating": 2.0, "directors": "Mid Band"},
            {"user_rating": 2.5, "directors": "Mid Band"},
            {"user_rating": 4.5, "directors": "Other"},
        ]
    )
    profile = build_affinity(df)
    affinity = profile.affinities["directors"]["Mid Band"]
    assert affinity < 0  # μ-relative ranking still sees the below-mean deviation
    recovered = _mean_rating(affinity, profile.counts["directors"]["Mid Band"], profile.mu)
    assert recovered == pytest.approx((2.5 + 2.0 + 2.5) / 3)


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


def test_score_cast_weight_and_nan_cast_neutral():
    profile = _profile({"cast": {"Lead": 0.5}})
    df = pd.DataFrame([{"cast": "Lead"}, {"cast": float("nan")}])
    scores = score_films(df, profile)
    assert scores.iloc[0] == pytest.approx(_logistic(0.4 * 0.5))
    assert scores.iloc[1] == pytest.approx(50.0)


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


def test_explain_topk_sorted_disliked_excluded():
    # "Bad" at -0.9 means a 1.65 mean rating — below the sentiment pivot, never a chip.
    profile = _profile({"directors": {"Great": 0.9}, "genres": {"Good": 0.5, "Bad": -0.9}})
    row = pd.Series({"directors": "Great", "genres": "Good, Bad"})
    top2 = explain(row, profile, top_k=2)
    assert [label for label, _ in top2] == ["Great", "Good"]
    assert explain(row, profile, top_k=1) == [top2[0]]
    assert all(contribution > 0 for _, contribution in top2)


def test_explain_contribution_formula_exact():
    profile = _profile({"directors": {"Great": 0.9}, "genres": {"Good": 0.5, "Bad": -0.9}})
    row = pd.Series({"directors": "Great", "genres": "Good, Bad"})
    contributions = dict(explain(row, profile, top_k=2))
    assert contributions["Great"] == pytest.approx(1.0 * 0.9 / 1)
    # m_d counts every known value, liked or not: "Bad" still splits the genres share.
    assert contributions["Good"] == pytest.approx(0.6 * 0.5 / 2)


def test_explain_all_disliked_returns_empty():
    # Means 2.1 and 1.65 — both below the sentiment pivot.
    profile = _profile({"directors": {"Meh": -0.6}, "genres": {"Bad": -0.9}})
    row = pd.Series({"directors": "Meh", "genres": "Bad"})
    assert explain(row, profile) == []


def test_explain_liked_band_negative_affinity_still_surfaces():
    # μ-relative sign must not decide chip membership: a value whose mean sits
    # in [SENTIMENT_PIVOT, μ) — negative affinity, "watchable-to-good" tier —
    # stays eligible, ranked after the genuinely positive contributors.
    profile = _profile({"genres": {"Good": 0.5, "Mid Band": -0.4}})  # means 3.75 and 2.4
    row = pd.Series({"genres": "Good, Mid Band"})
    top2 = explain(row, profile, top_k=2)
    assert [label for label, _ in top2] == ["Good", "Mid Band"]
    assert top2[1][1] < 0  # eligibility is tier-based even when the μ-relative contribution is negative


def test_explain_boundary_exact_pivot_is_liked():
    # a = -0.5 lands the mean exactly on SENTIMENT_PIVOT (2.25) — liked, not disliked.
    profile = _profile({"genres": {"Edge": -0.5}})
    row = pd.Series({"genres": "Edge"})
    assert [label for label, _ in explain(row, profile)] == ["Edge"]


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
    watchlist = pd.DataFrame(
        [
            {
                "tmdb_id": 1,
                "genres": "A",
                "themes": "Heist",
                "mini_themes": "Noir",
                "release_year": 1994,
                "cast": "Sterling Hayden",
                "country": "USA",
                "language": "English",
            }
        ]
    )
    out = attach_match(pd.DataFrame([{"tmdb_id": 1, "movie": "X"}]), watchlist, profile)
    assert out["themes"].iloc[0] == "Heist"
    assert out["mini_themes"].iloc[0] == "Noir"
    assert out["release_year"].iloc[0] == 1994
    assert out["cast"].iloc[0] == "Sterling Hayden"
    assert out["country"].iloc[0] == "USA"
    assert out["language"].iloc[0] == "English"


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
    lines = result.split("\n")
    assert lines[1].startswith("Rating scale:")
    assert "2.5–3 = good" in lines[1]
    assert "Favourite genres: Western" in result
    assert "Least favourite genres: Comedy" in result
    assert "Favourite directors (≥2 films rated): Howard Hawks" in result
    assert "Least favourite directors (≥2 films rated): Dany Boon" in result
    assert "Favourite eras: 1950s" in result


def test_format_actors_line_with_threshold(make_ratings):
    df = make_ratings(
        [
            {"user_rating": 5.0, "cast": "Toshiro Mifune, Cameo Once"},
            {"user_rating": 4.5, "cast": "Toshiro Mifune"},
            {"user_rating": 1.0, "cast": "Someone Else"},
        ]
    )
    result = format_taste_profile(build_affinity(df))
    assert "Favourite actors (≥2 films rated): Toshiro Mifune" in result
    # A single rated appearance stays below the threshold
    assert "Cameo Once" not in result


def test_format_actors_excludes_disliked(make_ratings):
    # Two rated films is enough evidence, but a sub-pivot actor (mean 1.25 < 2.25)
    # must never be labelled a favourite.
    df = make_ratings(
        [
            {"user_rating": 1.0, "cast": "Disliked Twice"},
            {"user_rating": 1.5, "cast": "Disliked Twice"},
            {"user_rating": 5.0, "cast": "Loved Lead"},
            {"user_rating": 4.5, "cast": "Loved Lead"},
        ]
    )
    result = format_taste_profile(build_affinity(df))
    assert "Favourite actors (≥2 films rated): Loved Lead" in result
    assert "Disliked Twice" not in result


def test_format_actors_include_liked_band_despite_negative_affinity(make_ratings):
    # The favourites guard is the sentiment pivot, not `a > 0`: a regular whose
    # mean sits in [2.25, μ≈2.57) stays eligible, ranked after the genuinely
    # loved; sub-pivot actors are still excluded.
    df = make_ratings(
        [
            {"user_rating": 2.5, "cast": "Mid Band"},
            {"user_rating": 2.0, "cast": "Mid Band"},
            {"user_rating": 2.5, "cast": "Mid Band"},
            {"user_rating": 1.0, "cast": "Truly Bad"},
            {"user_rating": 1.0, "cast": "Truly Bad"},
            {"user_rating": 4.5, "cast": "Loved Lead"},
            {"user_rating": 4.5, "cast": "Loved Lead"},
        ]
    )
    result = format_taste_profile(build_affinity(df))
    actors_line = next(line for line in result.split("\n") if line.startswith("Favourite actors"))
    assert actors_line == "Favourite actors (≥2 films rated): Loved Lead, Mid Band"
    assert "Truly Bad" not in result


def test_format_dislikes_use_pivot_not_affinity_sign(make_ratings):
    # R2 regression: a genre/director whose mean rating lands in [2.25, μ≈2.57)
    # carries negative affinity yet is "watchable-to-good" on the ladder — it
    # must not be branded least-favourite; genuinely sub-pivot values still are.
    df = make_ratings(
        [
            {"user_rating": 2.5, "genres": "Horror", "directors": "Mid Band"},
            {"user_rating": 2.0, "genres": "Horror", "directors": "Mid Band"},
            {"user_rating": 2.5, "genres": "Horror", "directors": "Mid Band"},
            {"user_rating": 1.0, "genres": "Comedy", "directors": "Truly Bad"},
            {"user_rating": 1.0, "genres": "Comedy", "directors": "Truly Bad"},
            {"user_rating": 4.5, "genres": "Western", "directors": "Loved"},
            {"user_rating": 4.5, "genres": "Western", "directors": "Loved"},
        ]
    )
    profile = build_affinity(df)
    assert profile.affinities["directors"]["Mid Band"] < 0  # ranking stays μ-centered
    lines = {line.split(": ")[0]: line for line in format_taste_profile(profile).split("\n")}
    assert lines["Least favourite genres"] == "Least favourite genres: Comedy"
    assert lines["Least favourite directors (≥2 films rated)"] == "Least favourite directors (≥2 films rated): Truly Bad"


def test_format_omits_empty_dimensions():
    result = format_taste_profile(build_affinity(pd.DataFrame({"user_rating": [3.0, 5.0]})))
    assert result.startswith("Average rating given: 4.0")
    assert "Rating scale:" in result
    assert "genres" not in result
    assert "directors" not in result
    assert "themes" not in result
    assert "eras" not in result


def test_format_empty_profile_sentinel():
    assert format_taste_profile(build_affinity(pd.DataFrame())) == "No rating history available."
