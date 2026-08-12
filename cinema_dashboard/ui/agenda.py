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

That constraint is exactly what ``ui.cart.render_plan_agenda`` gives up: putting
a real ``st.pills`` beside each row means the header can no longer share a blob
with them, so plan mode trades the sticky heading for selectable showtimes. It is
a *second* renderer for that reason, not a flag on this one, and it reuses
:func:`_agenda_row_html` (with ``show_times=False``) and
:func:`_agenda_day_head_html` rather than forking either.

Rows link exactly like every other movie surface, with one simplification: the
title is the only anchor in the row (time pills, the rating chip and the lens
badge are ``<span>``s, and there is deliberately no trailer chip), so it reuses
``.movie-card-link`` — inheriting that class's Streamlit specificity guard — and
nothing needs lifting back above its stretched ``::after`` overlay with
``z-index``. **Do not add a second link to a row**; that reintroduces the
nested-anchor problem ``ui.cards._movie_card_html`` had to solve.

A row whose frame carries a non-null ``_category`` (the Screening in Paris
page's lens vocabulary — see ``pages.paris.categorize``) gains a small
``.agenda-cat`` badge beside the rating chip plus an ``agenda-row--cat-*``
modifier for the row's left accent. Both stay ``<span>``/class-only — never an
anchor (see above) — and the calendar page's frame has no ``_category`` column,
so its rows render exactly as before.

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

#: Container-key prefix marking the day strip as a *wrapping* chip strip.
#: ``st.container(key=X)`` emits a ``st-key-X`` class, so the stylesheet can
#: select it (``[class*="st-key-chipstrip-"]``) to space the wrapped lines
#: apart.
#:
#: The day strip takes ``width="content"`` (the widget default), **not**
#: ``width="stretch"``: stretch makes each chip grow to fill its line, which
#: looks deliberate while they all fit on one — and then, the moment the strip
#: wraps, blows whatever lands alone on the last line up to the full page width
#: (a single "Mon 24" chip as wide as the screen). This strip's option count is
#: one per day in the data, so it *will* wrap.
#:
#: Every other segmented control on both pages keeps the stretched default:
#: the Paris lens strip holds at most four options and the Sort and Agenda/Map
#: controls two, so none of them wrap, and filling the width is the look there.
CHIP_STRIP_CONTAINER_PREFIX = "chipstrip-"

#: Lens-category badges, keyed by the exact ``_category`` values
#: ``pages.paris.categorize`` emits → (CSS slug, glyph, label). The glyph and
#: text ride together so the tint never carries the meaning alone (WCAG 1.4.1),
#: and an unknown value simply renders no badge.
_CATEGORY_BADGES: dict[str, tuple[str, str, str]] = {
    "new": ("new", "✨", "New to you"),
    "second_chance": ("second-chance", "🔄", "Second chance"),
    "rewatch": ("rewatch", "⭐", "Rewatch"),
}


def _time_pill_html(showtime: AgendaShowtime) -> str:
    """One showtime as a static pill: the time, plus the theater when known."""
    when = html.escape(pd.Timestamp(showtime.when).strftime("%H:%M"))
    if not showtime.theater:
        return f'<span class="time-pill">{when}</span>'
    venue = html.escape(showtime.theater)
    return f'<span class="time-pill">{when} <span class="time-pill-venue">{venue}</span></span>'


def _agenda_row_html(entry: AgendaEntry, profile: TasteProfile | None = None, *, show_times: bool = True) -> str:
    """One agenda row: thumbnail, title/meta, lens badge, time pills, match chips.

    Sections are omitted rather than rendered empty — a film with no runtime
    shows no runtime segment (not an em dash), and a row with no taste ``match``
    carries no ``.agenda-match`` block at all, which collapses the grid to two
    columns. The lens badge appears only when the row carries a non-null
    ``_category`` (the Paris page); the calendar frame has no such column and
    renders untouched.

    ``show_times=False`` drops the ``.agenda-times`` block for
    ``ui.cart.render_plan_agenda``, which replaces the static pills with a real
    ``st.pills`` widget beside the row. That renderer reuses this function rather
    than forking it, so the poster, title anchor, lens badge and match chips stay
    byte-identical between browse and plan mode.
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

    # isinstance, not truthiness: a Paris row outside every lens carries
    # None/NA and `bool(pd.NA)` raises; a calendar row has no column at all,
    # so `get` returns None and the row renders exactly as before.
    raw_category = row.get("_category")
    badge = _CATEGORY_BADGES.get(raw_category) if isinstance(raw_category, str) else None
    cat_chip = ""
    cat_class = ""
    if badge is not None:
        css_slug, glyph, cat_label = badge
        cat_chip = f'<span class="agenda-cat agenda-cat--{css_slug}">{glyph} {cat_label}</span>'
        cat_class = f" agenda-row--cat-{css_slug}"

    sub_html = f'<div class="agenda-sub">{facts}{rating_chip}{cat_chip}</div>' if facts or rating_chip or cat_chip else ""

    pills = "".join(_time_pill_html(s) for s in entry.showtimes) if show_times else ""
    times_html = f'<div class="agenda-times">{pills}</div>' if pills else ""

    match_html = match_chips_html(row, profile) if profile is not None else ""
    match_block = f'<div class="agenda-match">{match_html}</div>' if match_html else ""

    return (
        f'<div class="agenda-row{" agenda-row--linked" if slug else ""}{cat_class}">'
        f"{poster_html}"
        f'<div class="agenda-main">'
        f'<div class="agenda-title">{title_html}</div>'
        f"{sub_html}{times_html}"
        f"</div>"
        f"{match_block}"
        f"</div>"
    )


def _agenda_day_head_html(day: AgendaDay) -> str:
    """The day heading on its own.

    Factored out so ``ui.cart.render_plan_agenda`` reuses it instead of forking
    it. Plan mode is the one caller that emits it *without* its rows — a widget
    cannot live inside a markdown blob, so plan mode knowingly gives up the sticky
    behaviour this header has in :func:`agenda_day_html` (``.agenda-day--plan``
    unsticks it explicitly rather than leaving a declaration that does nothing).

    The relative labels are qualified with the full date ("Tonight · Tuesday 04
    August") so the heading is never ambiguous once the page has been left open
    across midnight.
    """
    full_date = day.day.strftime("%A %d %B")
    heading = day.label if day.label == full_date else f"{day.label} · {full_date}"
    count = day.film_count
    count_label = f"{count} film" if count == 1 else f"{count} films"
    return (
        f'<header class="agenda-day-head">'
        f'<span class="agenda-day-label">{html.escape(heading)}</span>'
        f'<span class="agenda-day-count">{html.escape(count_label)}</span>'
        f"</header>"
    )


def agenda_day_html(day: AgendaDay, profile: TasteProfile | None = None) -> str:
    """One whole day section — sticky header plus every row — as a single blob."""
    rows_html = "".join(_agenda_row_html(entry, profile) for entry in day.entries)
    return (
        f'<section class="agenda-day{" agenda-day--today" if day.is_today else ""}">'
        f"{_agenda_day_head_html(day)}"
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

    Sized ``width="content"`` and wrapped in a
    :data:`CHIP_STRIP_CONTAINER_PREFIX` container — see that constant for why
    this strip must not stretch.
    """
    if not chips:
        return None

    by_value = {(DAY_ALL if chip.day is None else chip.day.isoformat()): chip for chip in chips}
    with st.container(key=f"{CHIP_STRIP_CONTAINER_PREFIX}{key}"):
        selection = st.segmented_control(
            "Day",
            options=list(by_value),
            selection_mode="single",
            default=DAY_ALL,
            format_func=lambda value: f"{by_value[value].label} · {by_value[value].count}",
            key=key,
            label_visibility="collapsed",
            width="content",
        )
    if not isinstance(selection, str) or selection not in by_value or selection == DAY_ALL:
        return None
    try:
        return date.fromisoformat(selection)
    except ValueError:
        return None
