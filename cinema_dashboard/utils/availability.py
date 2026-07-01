"""
Free-time availability logic for the Watchlist Showtimes page.

Answers "can the user actually attend this screening?" given a work schedule
and their calendar. Two user concepts are deliberately distinct:

- **Day off** — a day the user is free *all day long* (e.g. a booked weekday
  off work): its daytime screenings count as watchable.
- **Unavailable** — a day the user is away (e.g. vacation): everything that
  day is excluded, even a weekend, a public holiday, or a marked day off.

A showtime is watchable when::

    free      = is_weekend OR is_holiday OR is_day_off OR time >= cutoff
    watchable = free AND NOT is_unavailable

On any "free day" the whole day counts, so the after-cutoff clause only ever
adds plain-working-day evenings; on free days it is already subsumed by the
day-level clauses.

Public API:
    french_public_holidays(years) -> frozenset[date]
    free_time_mask(showtimes, *, cutoff, days_off, unavailable) -> pd.Series

Kept Streamlit-free so the mask logic is unit-testable without an app context.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, time
from functools import cache

import holidays
import pandas as pd


@cache
def french_public_holidays(years: tuple[int, ...]) -> frozenset[date]:
    """Metropolitan-France public holidays for the given years.

    Includes the moving feasts (Easter Monday, Ascension, Whit Monday) that a
    hardcoded list would get wrong. No Alsace-Moselle subdivision extras —
    the showtimes are Paris theaters.
    """
    return frozenset(holidays.France(years=years).keys())


def free_time_mask(
    showtimes: pd.Series,
    *,
    cutoff: time,
    days_off: Iterable[date] = (),
    unavailable: Iterable[date] = (),
) -> pd.Series:
    """Boolean mask (aligned to ``showtimes``) of screenings the user can attend.

    ``showtimes`` holds naive Paris-local datetimes (or parseable strings, per
    the SHOWTIMES contract). ``cutoff`` is the earliest weekday hour the user
    is free (whole free days ignore it). NaT / unparseable rows come out
    ``False`` — a screening we can't place in time is never "attendable".
    """
    dts = pd.to_datetime(showtimes)
    dates = dts.dt.date

    years = tuple(sorted({d.year for d in dates.dropna()}))
    holiday_dates = french_public_holidays(years) if years else frozenset()

    is_weekend = dts.dt.dayofweek >= 5
    is_holiday = dates.isin(holiday_dates)
    is_day_off = dates.isin(set(days_off))
    # Minutes-since-midnight instead of .dt.time: NaT propagates to NaN and
    # compares False, where a time-object comparison would raise on None.
    after_cutoff = (dts.dt.hour * 60 + dts.dt.minute) >= (cutoff.hour * 60 + cutoff.minute)

    free = is_weekend | is_holiday | is_day_off | after_cutoff
    return (free & ~dates.isin(set(unavailable))).fillna(False)
