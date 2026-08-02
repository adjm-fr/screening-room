"""Tests for pages.database — pure helper functions.

``pages/database.py`` calls ``main()`` unconditionally at module import time —
the Streamlit multipage convention shared by every ``pages/*.py`` file (see
``st.Page(...)`` in ``app.py``; Streamlit executes each page's source with its
own ``__main__`` namespace rather than a plain ``import``, so this is the
correct pattern for the app itself). To import the module here for its pure
helper (:func:`pages.database._streaming_label`) without running the full page
against this developer's real on-disk movie database, ``movies_output_path``
is patched to ``None`` before the *first* import: ``main()`` then hits its
"OUTPUT_PATH is not set" early return and does no further work. The module is
cached in ``sys.modules`` after that first import, so it's safe to re-import
in each test.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_database_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    import pages.database  # noqa: F401  (import side effect: registers the module in sys.modules)


def test_streaming_label_flatrate_only():
    from pages.database import _streaming_label

    row = pd.Series({"flatrate": ["netflix"], "free": []})
    assert _streaming_label(row) == "netflix"


def test_streaming_label_free_only():
    from pages.database import _streaming_label

    row = pd.Series({"flatrate": [], "free": ["arte-tv"]})
    assert _streaming_label(row) == "arte-tv (free)"


def test_streaming_label_both():
    from pages.database import _streaming_label

    row = pd.Series({"flatrate": ["netflix"], "free": ["arte-tv"]})
    assert _streaming_label(row) == "netflix, arte-tv (free)"


def test_streaming_label_sorts_within_each_group():
    from pages.database import _streaming_label

    row = pd.Series({"flatrate": ["netflix", "canalplus"], "free": ["francetv", "arte-tv"]})
    assert _streaming_label(row) == "canalplus, netflix, arte-tv (free), francetv (free)"


def test_streaming_label_empty():
    from pages.database import _streaming_label

    row = pd.Series({"flatrate": [], "free": []})
    assert _streaming_label(row) == ""


def test_streaming_label_missing_columns_treated_as_empty():
    from pages.database import _streaming_label

    assert _streaming_label(pd.Series({})) == ""


def test_unresolved_summary_empty_input_returns_empty():
    from pages.database import _unresolved_summary

    result = _unresolved_summary(pd.DataFrame())
    assert result.empty


def test_unresolved_summary_collapses_to_one_row_per_film():
    from pages.database import _unresolved_summary

    df = pd.DataFrame(
        [
            {
                "movie": "Unknown Film",
                "original_title": None,
                "director": None,
                "release_year": 2024,
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2030-06-05 20:00"),
            },
            {
                "movie": "Unknown Film",
                "original_title": None,
                "director": None,
                "release_year": 2024,
                "theater_name": "MK2 Bastille",
                "showtimes": pd.Timestamp("2030-06-03 18:00"),
            },
        ]
    )
    result = _unresolved_summary(df)
    assert len(result) == 1
    row = result.iloc[0]
    assert row["movie"] == "Unknown Film"
    assert row["next_showtime"] == pd.Timestamp("2030-06-03 18:00")
    assert row["theaters"] == "Le Champo, MK2 Bastille"
    assert row["n_showtimes"] == 2


def test_unresolved_summary_sorts_by_next_showtime():
    from pages.database import _unresolved_summary

    df = pd.DataFrame(
        [
            {
                "movie": "Later Film",
                "original_title": None,
                "director": None,
                "release_year": 2024,
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2030-06-10 20:00"),
            },
            {
                "movie": "Sooner Film",
                "original_title": None,
                "director": None,
                "release_year": 2024,
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2030-06-02 20:00"),
            },
        ]
    )
    result = _unresolved_summary(df)
    assert result["movie"].tolist() == ["Sooner Film", "Later Film"]
