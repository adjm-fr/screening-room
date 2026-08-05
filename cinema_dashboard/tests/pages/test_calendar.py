"""Tests for pages.calendar — now an orchestration-only module.

``pages/calendar.py`` calls ``main()`` unconditionally at module import time
(the Streamlit multipage convention shared by every ``pages/*.py`` file). As in
``test_database.py``, ``movies_output_path`` is patched to ``None`` before the
*first* import so ``main()`` hits its "OUTPUT_PATH is not set" early return
instead of running against this developer's real on-disk parquets.

The page's logic now lives in libraries with their own suites: grouping,
labelling and the filter chain in ``tests/core/test_agenda.py``, the row/day
HTML in ``tests/ui/test_agenda.py``, and both export builders in
``tests/ui/test_ics.py``. What is worth asserting *here* is that the page still
imports cleanly under that early return, and that it has not re-implemented the
export off a frame of its own.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_calendar_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    import pages.calendar  # noqa: F401  (import side effect: registers the module in sys.modules)


def test_page_exposes_main_after_the_early_return():
    import pages.calendar

    assert callable(pages.calendar.main)


def test_export_uses_the_shared_builders():
    """Guards the "export mirrors on-screen filters" invariant mechanically.

    Both builders take the whole filtered frame, so as long as the page calls
    *these* functions it cannot be exporting something the agenda never showed.
    """
    import pages.calendar
    import ui.ics

    assert pages.calendar.build_ics_events is ui.ics.build_ics_events
    assert pages.calendar.build_csv_rows is ui.ics.build_csv_rows
