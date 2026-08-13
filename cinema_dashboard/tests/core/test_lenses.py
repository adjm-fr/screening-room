"""Tests for core.lenses — the Screening in Paris lens vocabulary.

Streamlit-free pure pandas, so these import the module directly rather than going
through ``pages.paris`` (which calls ``main()`` at import time). Every helper is
total: the recurring battery per function is empty frame, missing column, nullable
dtype, index preservation, and non-mutation of the input.
"""

from __future__ import annotations

import pandas as pd

# ── categorize ───────────────────────────────────────────────────────────────


def test_categorize_untracked_is_new():
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["untracked"], "user_rating": [pd.NA], "match": [55.0]})
    assert categorize(df).tolist() == ["new"]


def test_categorize_low_rating_high_match_is_second_chance():
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["seen"], "user_rating": [2.0], "match": [80.0]})
    assert categorize(df).tolist() == ["second_chance"]


def test_categorize_low_rating_low_match_is_uncategorised():
    """Below RETRY_MIN_MATCH the disagreement premise doesn't hold — no lens."""
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["seen"], "user_rating": [2.0], "match": [60.0]})
    assert categorize(df).isna().all()


def test_categorize_high_rating_is_rewatch():
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["seen"], "user_rating": [4.5], "match": [90.0]})
    assert categorize(df).tolist() == ["rewatch"]


def test_categorize_boundaries_are_exclusive_below_and_inclusive_at_the_floor():
    """2.5 is not < RETRY_MAX_RATING; 4.0 is >= REWATCH_MIN_RATING."""
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["seen", "seen"], "user_rating": [2.5, 4.0], "match": [99.0, 99.0]})
    out = categorize(df)
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == "rewatch"


def test_categorize_na_rating_never_fires_a_rating_cut():
    """A watchlist row carries no rating: NA must coerce to False, not poison the mask."""
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["watchlist"], "user_rating": [pd.NA], "match": [95.0]})
    assert categorize(df).isna().all()


def test_categorize_without_match_column_never_fires_second_chance():
    """No taste profile means no `match` column — the disagreement lens simply vanishes."""
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["seen"], "user_rating": [2.0]})
    assert categorize(df).isna().all()


def test_categorize_without_watch_status_never_fires_new():
    from pages.paris import categorize

    df = pd.DataFrame({"user_rating": [pd.NA], "match": [90.0]})
    assert categorize(df).isna().all()


def test_categorize_empty_frame_is_empty_not_an_error():
    from pages.paris import categorize

    out = categorize(pd.DataFrame())
    assert isinstance(out, pd.Series)
    assert out.empty


def test_categorize_handles_nullable_float64_user_rating():
    """`series >= x` on a Float64 column yields NA, which pandas rejects as a mask."""
    from pages.paris import categorize

    df = pd.DataFrame(
        {
            "watch_status": ["seen", "seen", "watchlist"],
            "user_rating": pd.array([2.0, 4.5, pd.NA], dtype="Float64"),
            "match": pd.array([80.0, pd.NA, 90.0], dtype="Float64"),
        }
    )
    assert categorize(df).tolist() == ["second_chance", "rewatch", None]


def test_categorize_is_aligned_to_the_frames_index():
    from pages.paris import categorize

    df = pd.DataFrame({"watch_status": ["untracked", "seen"], "user_rating": [pd.NA, 4.5], "match": [50.0, 50.0]}, index=[42, 7])
    out = categorize(df)
    assert list(out.index) == [42, 7]
    assert out.loc[42] == "new"
    assert out.loc[7] == "rewatch"


# ── drop_uninteresting_seen ─────────────────────────────────────────────────


def test_drop_uninteresting_seen_drops_seen_films_with_no_category():
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame({"watch_status": ["seen"], "_category": [None]})
    assert drop_uninteresting_seen(df).empty


def test_drop_uninteresting_seen_keeps_seen_second_chance_and_rewatch():
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame({"watch_status": ["seen", "seen"], "_category": ["second_chance", "rewatch"]})
    assert len(drop_uninteresting_seen(df)) == 2


def test_drop_uninteresting_seen_keeps_untracked_and_watchlist_regardless_of_category():
    """The drop only ever targets watch_status == "seen"."""
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame({"watch_status": ["untracked", "watchlist"], "_category": [None, None]})
    assert len(drop_uninteresting_seen(df)) == 2


def test_drop_uninteresting_seen_survives_missing_columns():
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame({"watch_status": ["seen"]})
    result = drop_uninteresting_seen(df)
    assert len(result) == 1

    df = pd.DataFrame({"_category": [None]})
    result = drop_uninteresting_seen(df)
    assert len(result) == 1


def test_drop_uninteresting_seen_does_not_mutate_input():
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame({"watch_status": ["seen"], "_category": [None]})
    drop_uninteresting_seen(df)
    assert len(df) == 1


def test_drop_uninteresting_seen_preserves_index():
    from pages.paris import drop_uninteresting_seen

    df = pd.DataFrame(
        {"watch_status": ["seen", "seen"], "_category": [None, "rewatch"]},
        index=[42, 7],
    )
    result = drop_uninteresting_seen(df)
    assert list(result.index) == [7]


# ── lens_counts ──────────────────────────────────────────────────────────────


def test_lens_counts_are_distinct_films_not_screenings():
    from pages.paris import lens_counts

    df = pd.DataFrame({"_category": ["new", "new", "rewatch", None], "_film_key": ["a", "a", "b", "c"]})
    assert lens_counts(df) == {"new": 1, "rewatch": 1}


def test_lens_counts_omits_zero_count_lenses_and_survives_missing_columns():
    from pages.paris import lens_counts

    assert lens_counts(pd.DataFrame({"_film_key": ["a"]})) == {}
    assert lens_counts(pd.DataFrame({"_category": ["new"]})) == {}
    assert lens_counts(pd.DataFrame()) == {}


def test_lens_counts_follows_display_order_not_data_order():
    """Chip order comes from LENS_LABELS, so it never shuffles with the data."""
    from pages.paris import LENS_LABELS, lens_counts

    df = pd.DataFrame({"_category": ["rewatch", "new", "second_chance"], "_film_key": ["a", "b", "c"]})
    assert list(lens_counts(df)) == list(LENS_LABELS)
