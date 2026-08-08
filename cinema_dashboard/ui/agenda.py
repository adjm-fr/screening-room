"""
Agenda rendering for the Watchlist Showtimes page: day sections and the day strip.

The Streamlit half of :mod:`core.agenda`. Turns the :class:`~core.agenda.AgendaDay`
list into the compact vertical agenda that replaced the page's per-day poster
rails — one row per (film × day), that day's showtimes as static
``.time-pill`` spans rather than widgets, because a showtime is information, not
a control.

**One ``st.markdown`` blob per day, never per row.** This is not only about
call count: Streamlit wraps every ``st.markdown`` in its own content-sized
element container, so a day header emitted separately from its rows would have a
containing block exactly its own height and ``position: sticky`` on
``.agenda-day-head`` would silently do nothing. The header and its rows must
share one blob for the sticky heading to work at all. Do not "clean this up"
into per-row calls.

Rows link exactly like every other movie surface, with one simplification: the
title is the only anchor in the row (time pills and the rating chip are
``<span>``s, and there is deliberately no trailer chip), so it reuses
``.movie-card-link`` — inheriting that class's Streamlit specificity guard — and
nothing needs lifting back above its stretched ``::after`` overlay with
``z-index``. **Do not add a second link to a row**; that reintroduces the
nested-anchor problem ``ui.cards._movie_card_html`` had to solve.

Public API:
    agenda_day_html(day, profile) -> str
    render_agenda(days, *, profile) -> None
    render_day_strip(chips, *, key) -> date | None
"""

from __future__ import annotations

import html
from datetime import date

import pandas as pd
import streamlit as st

from core.agenda import AgendaDay, AgendaEntry, AgendaShowtime, DayChip
from core.taste import TasteProfile
from ui.cards import _directors_of, _rating_chip_html, _title_of
from ui.chips import match_chips_html
from ui.theme import format_runtime, movie_href, row_slug

#: Sentinel option value for the "All days" chip. A ``None`` option would be
#: indistinguishable from "nothing selected" in the widget's return value.
DAY_ALL = "all"


def _time_pill_html(showtime: AgendaShowtime) -> str:
    """One showtime as a static pill: the time, plus the theater when known."""
    when = html.escape(pd.Timestamp(showtime.when).strftime("%H:%M"))
    if not showtime.theater:
        return f'<span class="time-pill">{when}</span>'
    venue = html.escape(showtime.theater)
    return f'<span class="time-pill">{when} <span class="time-pill-venue">{venue}</span></span>'


def _agenda_row_html(entry: AgendaEntry, profile: TasteProfile | None = None) -> str:
    """One agenda row: thumbnail, title/meta, that day's time pills, match chips.

    Sections are omitted rather than rendered empty — a film with no runtime
    shows no runtime segment (not an em dash), and a row with no taste ``match``
    carries no ``.agenda-match`` block at all, which collapses the grid to two
    columns.
    """
    row = entry.row
    title = _title_of(row)
    slug = row_slug(row)
    poster_url = row.get("poster_url")

    poster_html = (
        f'<img class="agenda-thumb" src="{html.escape(str(poster_url))}" alt="{html.escape(title)} poster" loading="lazy" />'
        if isinstance(poster_url, str) and poster_url
        else '<div class="skeleton agenda-thumb"></div>'
    )
    title_html = (
        f'<a class="movie-card-link" href="{movie_href(slug)}" target="_self">{html.escape(title)}</a>'
        if slug
        else html.escape(title)
    )

    directors = _directors_of(row)
    runtime = format_runtime(row.get("runtime_minutes"))
    facts = " · ".join(part for part in (html.escape(directors) if directors else "", runtime if runtime != "—" else "") if part)
    rating = row.get("letterboxd_avg_rating")
    rating_chip = _rating_chip_html(rating if isinstance(rating, (int, float)) else None)
    sub_html = f'<div class="agenda-sub">{facts}{rating_chip}</div>' if facts or rating_chip else ""

    pills = "".join(_time_pill_html(s) for s in entry.showtimes)
    times_html = f'<div class="agenda-times">{pills}</div>' if pills else ""

    match_html = match_chips_html(row, profile) if profile is not None else ""
    match_block = f'<div class="agenda-match">{match_html}</div>' if match_html else ""

    return (
        f'<div class="agenda-row{" agenda-row--linked" if slug else ""}">'
        f"{poster_html}"
        f'<div class="agenda-main">'
        f'<div class="agenda-title">{title_html}</div>'
        f"{sub_html}{times_html}"
        f"</div>"
        f"{match_block}"
        f"</div>"
    )


def agenda_day_html(day: AgendaDay, profile: TasteProfile | None = None) -> str:
    """One whole day section — sticky header plus every row — as a single blob.

    The relative labels are qualified with the full date ("Tonight · Tuesday 04
    August") so the heading is never ambiguous once the page has been left open
    across midnight.
    """
    full_date = day.day.strftime("%A %d %B")
    heading = day.label if day.label == full_date else f"{day.label} · {full_date}"
    count = day.film_count
    count_label = f"{count} film" if count == 1 else f"{count} films"
    rows_html = "".join(_agenda_row_html(entry, profile) for entry in day.entries)

    return (
        f'<section class="agenda-day{" agenda-day--today" if day.is_today else ""}">'
        f'<header class="agenda-day-head">'
        f'<span class="agenda-day-label">{html.escape(heading)}</span>'
        f'<span class="agenda-day-count">{html.escape(count_label)}</span>'
        f"</header>"
        f"{rows_html}"
        f"</section>"
    )


def render_agenda(days: list[AgendaDay], *, profile: TasteProfile | None = None) -> None:
    """Render the agenda, one ``st.markdown`` call per day section.

    Renders nothing for an empty list — the page owns the empty state, since only
    it knows whether "nothing" means no data, no matches, or no screenings on the
    selected day.
    """
    for day in days:
        st.markdown(agenda_day_html(day, profile), unsafe_allow_html=True)


def render_day_strip(chips: list[DayChip], *, key: str = "cal_day") -> date | None:
    """Render the day-selector strip; return the selected day (``None`` = all days).

    Options are ISO date strings rather than ``date`` objects so the value stored
    in session state is a stable scalar across reruns.

    The chips are rebuilt from the filtered frame on every rerun, so tightening
    any *other* filter can delete the day that is currently selected. Anything
    that does not resolve to an offered option falls back to "all" — showing the
    user every screening beats showing them an empty agenda.
    """
    if not chips:
        return None

    by_value = {(DAY_ALL if chip.day is None else chip.day.isoformat()): chip for chip in chips}
    selection = st.segmented_control(
        "Day",
        options=list(by_value),
        selection_mode="single",
        default=DAY_ALL,
        format_func=lambda value: f"{by_value[value].label} · {by_value[value].count}",
        key=key,
        label_visibility="collapsed",
        width="stretch",
    )
    if not isinstance(selection, str) or selection not in by_value or selection == DAY_ALL:
        return None
    try:
        return date.fromisoformat(selection)
    except ValueError:
        return None
