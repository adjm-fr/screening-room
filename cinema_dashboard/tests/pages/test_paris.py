"""Tests for pages.paris — pure helper functions.

Same import strategy as ``tests/pages/test_database.py``: ``pages/paris.py``
calls ``main()`` unconditionally at module import time (the Streamlit
multipage convention), so ``movies_output_path`` is patched to ``None``
*before* the first import — ``main()`` then hits its "configure your data
paths" early return and does no further work.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_paris_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    import pages.paris  # noqa: F401  (import side effect: registers the module in sys.modules)


def test_dedupe_key_prefers_slug():
    from pages.paris import _dedupe_key

    row = pd.Series({"letterboxd_slug": "dune-2021", "french_title": "Dune"})
    assert _dedupe_key(row) == "dune-2021"


def test_dedupe_key_falls_back_to_french_title_when_unmatched():
    from pages.paris import _dedupe_key

    row = pd.Series({"letterboxd_slug": None, "french_title": "Some Obscure Film"})
    assert _dedupe_key(row) == "Some Obscure Film"


def test_dedupe_key_handles_missing_columns():
    from pages.paris import _dedupe_key

    assert _dedupe_key(pd.Series({})) == ""


def test_dedupe_key_treats_nan_slug_as_absent():
    from pages.paris import _dedupe_key

    row = pd.Series({"letterboxd_slug": float("nan"), "french_title": "Titre"})
    assert _dedupe_key(row) == "Titre"


def _group(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_showtime_badges_missing_key_is_empty():
    from pages.paris import _showtime_badges_html

    assert _showtime_badges_html("dune-2021", {}) == ""


def test_showtime_badges_lists_theater_and_time():
    from pages.paris import _showtime_badges_html

    group = _group([{"showtimes": "2026-01-01 20:00", "theater_name": "Le Champo"}])
    html = _showtime_badges_html("dune-2021", {"dune-2021": group})
    assert "Le Champo" in html
    assert "20:00" in html
    assert html.count("showtime-badge") == 1


def test_showtime_badges_ordered_earliest_first():
    from pages.paris import _showtime_badges_html

    group = _group(
        [
            {"showtimes": "2026-01-02 18:00", "theater_name": "Late"},
            {"showtimes": "2026-01-01 20:00", "theater_name": "Early"},
        ]
    )
    html = _showtime_badges_html("dune-2021", {"dune-2021": group})
    assert html.index("Early") < html.index("Late")


def test_showtime_badges_caps_and_suffixes_the_overflow():
    from pages.paris import MAX_SHOWTIME_BADGES, _showtime_badges_html

    rows = [{"showtimes": f"2026-01-{d:02d} 20:00", "theater_name": "T"} for d in range(1, MAX_SHOWTIME_BADGES + 4)]
    html = _showtime_badges_html("dune-2021", {"dune-2021": _group(rows)})
    assert html.count("showtime-badge") == MAX_SHOWTIME_BADGES + 1
    assert "+3 more" in html


def test_showtime_badges_html_escapes_theater_name():
    from pages.paris import _showtime_badges_html

    group = _group([{"showtimes": "2026-01-01 20:00", "theater_name": "<script>Le Champo</script>"}])
    html = _showtime_badges_html("dune-2021", {"dune-2021": group})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
