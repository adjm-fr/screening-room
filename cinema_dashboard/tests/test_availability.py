import datetime as dt

import pandas as pd
from utils.availability import free_time_mask, french_public_holidays

# 2026 anchors: Mon 2026-06-29 is a plain working day; Sat 2026-07-04 / Sun
# 2026-07-05 are a weekend; Tue 2026-07-14 is Bastille Day; Mon 2026-04-06 is
# Easter Monday (a moving feast a hardcoded list would miss).
WORKDAY = "2026-06-29"
SATURDAY = "2026-07-04"
BASTILLE = "2026-07-14"
EASTER_MONDAY = "2026-04-06"

CUTOFF = dt.time(19, 0)


def _mask(showtimes: list[str | None], **kwargs) -> list[bool]:
    kwargs.setdefault("cutoff", CUTOFF)
    return free_time_mask(pd.Series(showtimes), **kwargs).tolist()


# ---------------------------------------------------------------------------
# french_public_holidays
# ---------------------------------------------------------------------------


def test_holidays_include_fixed_and_moving_feasts():
    hols = french_public_holidays((2026,))
    assert dt.date(2026, 7, 14) in hols  # Bastille Day (fixed)
    assert dt.date(2026, 4, 6) in hols  # Easter Monday (moving)
    assert dt.date(2026, 6, 29) not in hols


def test_holidays_result_is_cached():
    assert french_public_holidays((2026,)) is french_public_holidays((2026,))


# ---------------------------------------------------------------------------
# free_time_mask — the four "free" clauses
# ---------------------------------------------------------------------------


def test_weekend_midday_included():
    assert _mask([f"{SATURDAY} 14:00"]) == [True]


def test_workday_before_cutoff_excluded():
    assert _mask([f"{WORKDAY} 14:00"]) == [False]


def test_workday_at_and_after_cutoff_included():
    assert _mask([f"{WORKDAY} 19:00", f"{WORKDAY} 21:30"]) == [True, True]


def test_cutoff_is_modifiable():
    show = f"{WORKDAY} 18:30"
    assert _mask([show], cutoff=dt.time(18, 0)) == [True]
    assert _mask([show], cutoff=dt.time(20, 0)) == [False]


def test_weekday_holiday_included_all_day():
    assert _mask([f"{BASTILLE} 11:00", f"{EASTER_MONDAY} 11:00"]) == [True, True]


def test_day_off_includes_daytime_on_that_day_only():
    day_off = dt.date(2026, 6, 29)
    shows = [f"{WORKDAY} 14:00", "2026-06-30 14:00"]
    assert _mask(shows, days_off=[day_off]) == [True, False]


# ---------------------------------------------------------------------------
# free_time_mask — unavailable overrides everything
# ---------------------------------------------------------------------------


def test_unavailable_overrides_weekend_holiday_and_evening():
    shows = [f"{SATURDAY} 14:00", f"{BASTILLE} 11:00", f"{WORKDAY} 20:00"]
    unavailable = [dt.date(2026, 7, 4), dt.date(2026, 7, 14), dt.date(2026, 6, 29)]
    assert _mask(shows, unavailable=unavailable) == [False, False, False]


def test_unavailable_wins_over_day_off():
    day = dt.date(2026, 6, 29)
    assert _mask([f"{WORKDAY} 14:00"], days_off=[day], unavailable=[day]) == [False]


# ---------------------------------------------------------------------------
# free_time_mask — robustness
# ---------------------------------------------------------------------------


def test_nat_showtime_excluded_without_raising():
    assert _mask([None, f"{SATURDAY} 14:00"]) == [False, True]


def test_empty_series_returns_empty_mask():
    assert _mask([]) == []


def test_mask_preserves_index():
    series = pd.Series([f"{SATURDAY} 14:00", f"{WORKDAY} 10:00"], index=[7, 3])
    result = free_time_mask(series, cutoff=CUTOFF)
    assert list(result.index) == [7, 3]
    assert result.loc[7] and not result.loc[3]
