"""Tests for pages.calendar — the ICS export builder.

``pages/calendar.py`` calls ``main()`` unconditionally at module import time
(the Streamlit multipage convention shared by every ``pages/*.py`` file). As in
``test_database.py``, ``movies_output_path`` is patched to ``None`` before the
*first* import so ``main()`` hits its "OUTPUT_PATH is not set" early return
instead of running against this developer's real on-disk parquets.

The duration helpers these events are sized with (``screening_end``,
``_ads_minutes``) now live in ``ui.ics`` beside ``to_ics``, shared with the
movie detail page's per-screening download — they are tested in
``tests/ui/test_ics.py``. What stays here is that this page's export *uses* them.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_calendar_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    import pages.calendar  # noqa: F401  (import side effect: registers the module in sys.modules)


def test_build_ics_events_ends_include_ads():
    from pages.calendar import _build_ics_events

    df = pd.DataFrame(
        [
            {
                "showtimes": "2026-08-03 20:00",
                "runtime_minutes": 100,
                "theater_name": "UGC Ciné Cité Bercy",
                "french_title": "Film A",
                "letterboxd_title": "Film A",
                "directors": "Alice",
            },
            {
                "showtimes": "2026-08-04 18:00",
                "runtime_minutes": 100,
                "theater_name": "Le Champo",
                "french_title": "Film B",
                "letterboxd_title": "Film B",
                "directors": "Bob",
            },
        ]
    )

    events = _build_ics_events(df)

    assert [e["end"] for e in events] == [pd.Timestamp("2026-08-03 22:00"), pd.Timestamp("2026-08-04 19:50")]


def test_build_ics_events_skips_rows_without_showtime():
    from pages.calendar import _build_ics_events

    df = pd.DataFrame(
        [
            {
                "showtimes": None,
                "runtime_minutes": 100,
                "theater_name": "MK2 Quai de Seine",
                "french_title": "Film A",
                "letterboxd_title": "Film A",
                "directors": "Alice",
            }
        ]
    )

    assert _build_ics_events(df) == []
