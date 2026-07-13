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
    module_mocker.patch("modules.config.settings.movies_output_path", None)
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
