"""Tests for pages.0_home — the streaming-rail frame helper.

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
"""

from __future__ import annotations

import importlib

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def home_page(module_mocker):
    module_mocker.patch("modules.config.settings.movies_output_path", None)
    return importlib.import_module("pages.0_home")


def test_streaming_rail_frame_resolves_canonical_letterboxd_title(home_page, make_watchlist):
    # Regression test: watchlist_df carries `title` (Letterboxd's canonical
    # title) and `french_title` (TMDB's French retitle) but never
    # `letterboxd_title`. utils.ui._movie_card_html resolves the display
    # title in letterboxd_title -> french_title -> title -> movie order, so
    # without the rename this rail fell through to the French title while
    # every other surface (streaming.py, database.py, chat.py) showed the
    # canonical one. No `tmdb_id` column keeps attach_streaming on its no-op
    # branch, so this stays filesystem-free.
    watchlist_df = make_watchlist([{"title": "Sudden Fear", "french_title": "Le Masque arraché"}])

    result = home_page._streaming_rail_frame(watchlist_df, "unused")

    assert result.iloc[0]["letterboxd_title"] == "Sudden Fear"
    assert result.iloc[0]["french_title"] == "Le Masque arraché"
    assert result.iloc[0]["flatrate"] == []
    assert result.iloc[0]["free"] == []


def test_streaming_rail_frame_preserves_existing_letterboxd_title(home_page, make_watchlist):
    # If letterboxd_title is already present, the rename must not clobber it
    # with title (mirrors the same guard in pages/streaming.py).
    watchlist_df = make_watchlist([{"title": "Original Title", "letterboxd_title": "Canonical Title"}])

    result = home_page._streaming_rail_frame(watchlist_df, "unused")

    assert result.iloc[0]["letterboxd_title"] == "Canonical Title"


def test_streaming_rail_frame_missing_title_column_is_a_noop(home_page):
    # No `title` column at all: the rename guard must not raise or invent one.
    watchlist_df = pd.DataFrame([{"slug": "no-title-film"}])

    result = home_page._streaming_rail_frame(watchlist_df, "unused")

    assert "letterboxd_title" not in result.columns
