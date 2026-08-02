"""
The "Only times I'm free" control — the Streamlit half of ``core.availability``.

``core.availability.free_time_mask`` owns the rule (weekend / holiday / day off
/ weekday-after-cutoff, minus days marked unavailable); this module owns the
widgets that gather its arguments, so the Watchlist Showtimes and Screening in
Paris pages present one identical control instead of two hand-kept-in-sync
copies of the same toggle and three pickers.

Rendering is deliberately split from applying, because the two pages need the
mask at different points in their pipelines: the calendar renders the control
at the page top (above the sidebar filters) but applies it *late*, as one link
in a longer filter chain whose final frame also feeds the ICS/CSV export, while
Screening in Paris applies it immediately so every curated rail below is built
from attendable screenings only. :func:`render_free_time_filter` therefore
returns a :class:`FreeTimeSelection` the caller applies wherever it belongs.

Public API:
    FreeTimeSelection            frozen selection + ``.apply(rows)``
    render_free_time_filter(...) render the toggle (+ pickers when on)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core.availability import free_time_mask

#: Default "free from" hour on a plain working weekday.
DEFAULT_CUTOFF = dt.time(19, 0)

_HELP = (
    "Weekends, French public holidays, your days off, or weekday screenings "
    "at/after the cutoff — minus any days you've marked unavailable."
)


@dataclass(frozen=True)
class FreeTimeSelection:
    """What the user picked in the free-time control, plus how to apply it.

    ``enabled`` is the toggle itself: when off, :meth:`apply` is a no-op and
    the other fields carry their defaults (the widgets aren't rendered at all,
    so there is nothing to read).
    """

    enabled: bool
    cutoff: dt.time = DEFAULT_CUTOFF
    days_off: tuple[dt.date, ...] = ()
    unavailable: tuple[dt.date, ...] = ()

    def apply(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Narrow ``rows`` to screenings the user can attend (unchanged when disabled)."""
        if not self.enabled or rows.empty:
            return rows
        return rows[
            free_time_mask(
                rows["showtimes"],
                cutoff=self.cutoff,
                days_off=self.days_off,
                unavailable=self.unavailable,
            )
        ]


def render_free_time_filter(rows: pd.DataFrame, *, key_prefix: str) -> FreeTimeSelection:
    """Render the toggle, plus the cutoff/day pickers inline when it's on.

    ``rows`` supplies the date options for the two day pickers — pass the frame
    whose showtimes define the horizon (typically the *unfiltered* one, so
    narrowing another filter can't silently drop a date the user wanted to
    mark). ``key_prefix`` namespaces the four widget keys, since both pages can
    live in one session.
    """
    enabled = st.toggle("Only times I'm free", value=False, key=f"{key_prefix}_free", help=_HELP)
    if not enabled:
        return FreeTimeSelection(enabled=False)

    upcoming = sorted(pd.to_datetime(rows["showtimes"]).dt.date.dropna().unique())
    col_cutoff, col_off, col_away = st.columns([1, 2, 2])
    with col_cutoff:
        cutoff = st.time_input(
            "Free from (weekdays)", value=DEFAULT_CUTOFF, step=dt.timedelta(minutes=15), key=f"{key_prefix}_cutoff"
        )
    with col_off:
        days_off = st.multiselect(
            "Days off (free all day)", upcoming, key=f"{key_prefix}_daysoff", format_func=lambda d: d.strftime("%a %d %b")
        )
    with col_away:
        unavailable = st.multiselect(
            "Unavailable (away)", upcoming, key=f"{key_prefix}_unavail", format_func=lambda d: d.strftime("%a %d %b")
        )
    return FreeTimeSelection(
        enabled=True,
        cutoff=cutoff,
        days_off=tuple(days_off),
        unavailable=tuple(unavailable),
    )
