"""Tests for pages.0_home — the streaming-rail frame.

``pages/0_home.py`` calls ``main()`` unconditionally at module import time
(the Streamlit multipage convention shared by every ``pages/*.py`` file). As
in ``test_database.py`` and ``test_calendar.py``, ``movies_output_path`` is
patched to ``None`` before the *first* import so ``main()`` hits its
"OUTPUT_PATH is not set" early return instead of running against this
developer's real on-disk parquets.

The filename starts with a digit (Streamlit's convention for pinning page
order in ``st.navigation``), which isn't a valid dotted-import identifier —
``from pages.0_home import ...`` is a ``SyntaxError``. Tests therefore load
the module via :func:`importlib.import_module` and read attributes off the
returned module object instead of a bare ``import`` statement.

``_streaming_rail_frame`` owns the whole rail — rename, availability filter,
ranking, dedupe and cap — so these tests cover the rail end to end and
``main()`` holds nothing worth regressing. ``attach_streaming`` is patched
per test so provider membership is an input rather than a parquet read.
"""

from __future__ import annotations

import importlib

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def home_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    return importlib.import_module("pages.0_home")


@pytest.fixture
def rail(home_page, mocker):
    """Call ``_streaming_rail_frame`` with ``attach_streaming`` stubbed out.

    The stub echoes the frame it is handed and stamps on the caller's
    ``flatrate``/``free`` lists, so provider membership is controlled directly
    and no streaming parquet is read. It also records that frame, which is how
    the rename assertions below observe what the rail actually renders from.
    """
    seen: dict[str, pd.DataFrame] = {}

    def _call(watchlist_df, *, flatrate, free, subscribed=frozenset(), profile=None):
        def _fake_attach(df, movies_output):
            seen["frame"] = df
            out = df.copy()
            out["flatrate"] = flatrate
            out["free"] = free
            return out

        mocker.patch.object(home_page, "attach_streaming", side_effect=_fake_attach)
        result = home_page._streaming_rail_frame(watchlist_df, "unused", subscribed=subscribed, profile=profile)
        return result, seen["frame"]

    return _call


def test_rail_renders_the_canonical_letterboxd_title(rail, make_watchlist):
    # Regression test: watchlist_df carries `title` (Letterboxd's canonical
    # title) and `french_title` (TMDB's French retitle) but never
    # `letterboxd_title`. ui.cards._movie_card_html resolves the display title
    # in letterboxd_title -> french_title -> title -> movie order, so without
    # the rename this rail fell through to the French title while every other
    # surface (streaming.py, database.py, chat/ui.py) showed the canonical one.
    watchlist_df = make_watchlist(
        [{"title": "Sudden Fear", "french_title": "Le Masque arraché", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}]
    )

    result, _ = rail(watchlist_df, flatrate=[["netflix"]], free=[[]], subscribed={"netflix"})

    assert result.iloc[0]["letterboxd_title"] == "Sudden Fear"
    assert result.iloc[0]["french_title"] == "Le Masque arraché"


def test_rail_preserves_an_existing_letterboxd_title(rail, make_watchlist):
    # The rename must not clobber a letterboxd_title that is already present
    # (mirrors the same guard in pages/streaming.py).
    watchlist_df = make_watchlist(
        [{"title": "Original", "letterboxd_title": "Canonical", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}]
    )

    result, _ = rail(watchlist_df, flatrate=[["netflix"]], free=[[]], subscribed={"netflix"})

    assert result.iloc[0]["letterboxd_title"] == "Canonical"


def test_rail_without_a_title_column_is_a_noop(rail):
    # No `title` column at all: the rename guard must not raise or invent one.
    watchlist_df = pd.DataFrame([{"slug": "no-title-film", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}])

    _, frame = rail(watchlist_df, flatrate=[["netflix"]], free=[[]], subscribed={"netflix"})

    assert "letterboxd_title" not in frame.columns


def test_rail_keeps_subscribed_flatrate_and_drops_the_rest(rail, make_watchlist):
    watchlist_df = make_watchlist(
        [
            {"title": "Subscribed", "tmdb_id": "1", "letterboxd_avg_rating": 3.5},
            {"title": "Not subscribed", "tmdb_id": "2", "letterboxd_avg_rating": 3.0},
        ]
    )

    result, _ = rail(watchlist_df, flatrate=[["netflix"], ["mubi"]], free=[[], []], subscribed={"netflix"})

    assert result["letterboxd_title"].tolist() == ["Subscribed"]


def test_rail_keeps_free_providers_regardless_of_subscriptions(rail, make_watchlist):
    # Free platforms (Arte.tv, France.tv, …) are watchable by everyone and are
    # never gated by STREAMING_SERVICES.
    watchlist_df = make_watchlist([{"title": "On Arte", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}])

    result, _ = rail(watchlist_df, flatrate=[[]], free=[["artetv"]], subscribed={"netflix"})

    assert result["letterboxd_title"].tolist() == ["On Arte"]


def test_rail_falls_back_to_any_provider_without_subscriptions(rail, make_watchlist):
    # STREAMING_SERVICES unset: the rail is still useful, so any flatrate counts.
    watchlist_df = make_watchlist([{"title": "Anywhere", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}])

    result, _ = rail(watchlist_df, flatrate=[["mubi"]], free=[[]], subscribed=frozenset())

    assert result["letterboxd_title"].tolist() == ["Anywhere"]


def test_rail_is_empty_when_nothing_is_available(rail, make_watchlist):
    watchlist_df = make_watchlist([{"title": "Nowhere", "tmdb_id": "1", "letterboxd_avg_rating": 3.5}])

    result, _ = rail(watchlist_df, flatrate=[[]], free=[[]], subscribed={"netflix"})

    assert result.empty


def test_rail_ranks_by_community_rating_without_a_profile(rail, make_watchlist):
    watchlist_df = make_watchlist(
        [
            {"title": "Lower", "tmdb_id": "1", "letterboxd_avg_rating": 2.0},
            {"title": "Higher", "tmdb_id": "2", "letterboxd_avg_rating": 4.5},
        ]
    )

    result, _ = rail(watchlist_df, flatrate=[["netflix"], ["netflix"]], free=[[], []], subscribed={"netflix"})

    assert result["letterboxd_title"].tolist() == ["Higher", "Lower"]


def test_rail_ranks_by_taste_match_when_a_profile_exists(rail, home_page, make_watchlist, make_ratings):
    # The profile loves Horror and is lukewarm on Drama, so the Horror film
    # outranks the Drama one despite its lower community rating.
    ratings_df = make_ratings(
        [
            {"genres": "Horror", "user_rating": 5.0},
            {"genres": "Horror", "user_rating": 5.0},
            {"genres": "Drama", "user_rating": 1.0},
            {"genres": "Drama", "user_rating": 1.0},
        ]
    )
    profile = home_page.build_affinity(ratings_df)
    watchlist_df = make_watchlist(
        [
            {"title": "Beloved genre", "tmdb_id": "1", "genres": "Horror", "letterboxd_avg_rating": 3.0},
            {"title": "Disliked genre", "tmdb_id": "2", "genres": "Drama", "letterboxd_avg_rating": 4.0},
        ]
    )

    result, _ = rail(
        watchlist_df,
        flatrate=[["netflix"], ["netflix"]],
        free=[[], []],
        subscribed={"netflix"},
        profile=profile,
    )

    assert result["letterboxd_title"].tolist() == ["Beloved genre", "Disliked genre"]
    assert result["match"].is_monotonic_decreasing


def test_rail_dedupes_by_tmdb_id_and_caps_at_the_rail_size(rail, home_page, make_watchlist):
    size = home_page.STREAMING_RAIL_SIZE
    rows = [{"title": f"Film {i}", "tmdb_id": str(i), "letterboxd_avg_rating": 5.0 - i * 0.1} for i in range(size + 3)]
    rows.append({"title": "Duplicate id", "tmdb_id": "0", "letterboxd_avg_rating": 0.1})
    watchlist_df = make_watchlist(rows)

    result, _ = rail(
        watchlist_df,
        flatrate=[["netflix"]] * len(rows),
        free=[[]] * len(rows),
        subscribed={"netflix"},
    )

    assert len(result) == size
    assert result["tmdb_id"].is_unique
    assert "Duplicate id" not in result["letterboxd_title"].tolist()
