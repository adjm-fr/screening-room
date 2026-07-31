"""Tests for pages.calendar — the export's screening-duration helpers.

``pages/calendar.py`` calls ``main()`` unconditionally at module import time
(the Streamlit multipage convention shared by every ``pages/*.py`` file). As in
``test_database.py``, ``movies_output_path`` is patched to ``None`` before the
*first* import so ``main()`` hits its "OUTPUT_PATH is not set" early return
instead of running against this developer's real on-disk parquets.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_calendar_page(module_mocker):
    module_mocker.patch("modules.config.settings.movies_output_path", None)
    import pages.calendar  # noqa: F401  (import side effect: registers the module in sys.modules)


@pytest.mark.parametrize(
    "theater_name",
    ["MK2 Bibliothèque", "mk2 Odéon", "UGC Ciné Cité Les Halles", "ugc les halles", "Ugc Normandie"],
)
def test_ads_minutes_chain_theaters_case_insensitive(theater_name):
    from pages.calendar import ADS_MINUTES_CHAIN, _ads_minutes

    assert _ads_minutes(theater_name) == ADS_MINUTES_CHAIN == 20


@pytest.mark.parametrize("theater_name", ["Le Champo", "Christine Cinéma Club", "Cinémathèque Française"])
def test_ads_minutes_other_theaters(theater_name):
    from pages.calendar import ADS_MINUTES_DEFAULT, _ads_minutes

    assert _ads_minutes(theater_name) == ADS_MINUTES_DEFAULT == 10


@pytest.mark.parametrize("theater_name", [None, "", float("nan")])
def test_ads_minutes_missing_theater_falls_back_to_default(theater_name):
    from pages.calendar import ADS_MINUTES_DEFAULT, _ads_minutes

    assert _ads_minutes(theater_name) == ADS_MINUTES_DEFAULT


def test_screening_end_adds_chain_ads_to_runtime():
    from pages.calendar import _screening_end

    row = pd.Series({"runtime_minutes": 112, "theater_name": "MK2 Beaubourg"})
    start = pd.Timestamp("2026-08-03 19:30")

    assert _screening_end(row, start) == pd.Timestamp("2026-08-03 21:42")  # 20 ads + 112


def test_screening_end_adds_default_ads_to_runtime():
    from pages.calendar import _screening_end

    row = pd.Series({"runtime_minutes": 112, "theater_name": "Le Champo"})
    start = pd.Timestamp("2026-08-03 19:30")

    assert _screening_end(row, start) == pd.Timestamp("2026-08-03 21:32")  # 10 ads + 112


@pytest.mark.parametrize("runtime", [None, float("nan"), "", "not-a-number"])
def test_screening_end_unusable_runtime_falls_back_to_120_plus_ads(runtime):
    from pages.calendar import _screening_end

    row = pd.Series({"runtime_minutes": runtime, "theater_name": "UGC Danton"})
    start = pd.Timestamp("2026-08-03 19:30")

    assert _screening_end(row, start) == pd.Timestamp("2026-08-03 21:50")  # 20 ads + 120 fallback


def test_screening_end_missing_theater_column():
    from pages.calendar import _screening_end

    row = pd.Series({"runtime_minutes": 90})
    start = pd.Timestamp("2026-08-03 19:30")

    assert _screening_end(row, start) == pd.Timestamp("2026-08-03 21:10")  # 10 ads + 90


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
