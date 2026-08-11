"""
Watchlist Screenings page — a compact agenda of what's on and when.

Joins ``watchlist_with_letterboxd.parquet`` with ``showtimes.parquet`` and
renders the result as a **vertical agenda**: one section per day, one row per
(film × day), that day's showtimes as time pills inside the row. It replaced a
horizontal poster rail per day, which spent roughly 390px of height and a
sideways drag per film on a page whose whole job is "what can I see, and when?".
Grouping, labelling and filtering all live in ``core.agenda``; the row and
day-header HTML lives in ``ui.agenda``. This module only orchestrates.

**One filter chain, one frame.** :func:`core.agenda.apply_filters` applies every
control except the day strip, :func:`core.agenda.apply_day` folds that in, and
the single ``filtered`` frame it produces is what the agenda, the ICS export,
the CSV export and the map all read. Add a filter by extending
:class:`~core.agenda.AgendaFilters` — never by narrowing again further down, which
is how the download and the screen would silently diverge. A corollary worth
knowing: **picking a day scopes the export too**, so "download tonight only" is
one click on a day chip.

Controls, all in a toolbar above the agenda rather than a sidebar (this was the
only page in the app with one, and a sidebar is collapsed by default on the
phone this page is most useful on):

- a **day strip** of chips with per-day screening counts, which replaced a date
  range picker — the horizon is about a week, so a range control over ~10 chips
  was duplicating a strip that fits on one line;
- **search** across both title spellings and directors;
- a **Filters** popover carrying the low-frequency controls (theaters, runtime
  buckets, minimum Letterboxd rating), badged with the number of active filters;
- **time-of-day chips** (Morning/Afternoon/Evening/Late), which replaced a
  15-minute time-range slider — 96 stops for a decision with four real answers;
- the shared **"Only times I'm free"** control (:func:`ui.render_free_time_filter`,
  also used by Screening in Paris), whose selection feeds ``AgendaFilters``
  rather than being applied on the spot, so it stays inside the one chain;
- a **Time / Match** sort, which reorders entries *within* each day and never
  across days — the day strip is itself a day picker, so a flat list would leave
  that control pointing at nothing. The Match option only renders when there is
  a ratings history to build a taste profile from.

The ICS/CSV builders live in ``ui.ics`` beside :func:`ui.screening_end`, which
sizes every calendar block by padding the film's runtime with the pre-feature ad
block (20min in an MK2/UGC, 10min elsewhere) — the movie detail page's
per-screening ``.ics`` uses the identical helper so the three can't drift.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
import streamlit as st

from core.agenda import (
    RUNTIME_BUCKETS,
    TIME_BUCKET_LABELS,
    AgendaFilters,
    agenda_kpis,
    apply_day,
    apply_filters,
    build_agenda,
    day_chips,
)
from core.taste import TasteProfile, attach_match, build_affinity
from sources.geo import load_geocoded_theaters, render_theater_map
from sources.loader import (
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from ui import (
    build_csv_rows,
    build_ics_events,
    render_agenda,
    render_chip_filter,
    render_day_strip,
    render_empty_state,
    render_free_time_filter,
    render_freshness_banner,
    render_kpi_strip,
    to_ics,
)

SORT_TIME = "⏱ Time"
SORT_MATCH = "◎ Match"
VIEW_AGENDA = "Agenda"
VIEW_MAP = "Map"


def _filters_badge() -> str:
    """Label for the Filters popover, counting the controls that are away from default.

    Read from session state rather than from the live values, because the label
    has to be rendered *before* the popover's own widgets run. Streamlit reruns
    on every interaction, so the count the user actually sees is always current.
    """
    state = st.session_state
    prior = AgendaFilters(
        search=str(state.get("cal_search") or ""),
        theaters=tuple(state.get("cal_theaters") or ()),
        runtimes=tuple(state.get("cal_runtime") or ()),
        time_buckets=tuple(state.get("cal_tod") or ()),
        min_rating=float(state.get("cal_minrating") or 0.0),
        only_free=bool(state.get("cal_free")),
    )
    count = prior.active_count()
    return f"Filters · {count}" if count else "Filters"


def _render_toolbar(
    wl_shows: pd.DataFrame,
    *,
    has_profile: bool,
) -> tuple[AgendaFilters, Literal["time", "match"], str, st.delta_generator.DeltaGenerator]:
    """Render every control and return the selection, the sort mode, the view, and the export slot.

    The export popover is returned as an empty container rather than rendered
    here: its downloads are built from the *filtered* frame, which does not exist
    until these filters have been applied.
    """
    col_search, col_filters, col_sort, col_export, col_view = st.columns([5, 2, 3, 2, 2], vertical_alignment="bottom")

    with col_search:
        search = st.text_input(
            "Search title or director",
            key="cal_search",
            placeholder="🔍  Search title or director",
            label_visibility="collapsed",
        )

    with col_filters, st.popover(_filters_badge(), icon=":material/tune:", use_container_width=True):
        theaters = sorted(wl_shows["theater_name"].dropna().unique().tolist()) if "theater_name" in wl_shows.columns else []
        # No default: an empty selection means "all theaters", so the long list
        # stays inside the dropdown instead of rendering as a wall of tags.
        sel_theaters = st.multiselect("Theaters", theaters, key="cal_theaters", placeholder="All theaters")
        sel_runtime = render_chip_filter("Runtime", list(RUNTIME_BUCKETS), key="cal_runtime")
        min_rating = st.slider("Min Letterboxd rating", 0.0, 5.0, 0.0, 0.5, key="cal_minrating")

    sort_mode: Literal["time", "match"] = "time"
    if has_profile:
        with col_sort:
            # Built conditionally rather than disabled: with no ratings history
            # there is no Match to sort by, so render no control at all.
            choice = st.segmented_control(
                "Sort",
                [SORT_TIME, SORT_MATCH],
                default=SORT_TIME,
                key="cal_sort",
                label_visibility="collapsed",
                width="stretch",
            )
        sort_mode = "match" if choice == SORT_MATCH else "time"

    export_slot = col_export.container()

    with col_view:
        view = st.segmented_control(
            "View",
            [VIEW_AGENDA, VIEW_MAP],
            default=VIEW_AGENDA,
            key="cal_view",
            label_visibility="collapsed",
            width="stretch",
        )

    sel_tod = render_chip_filter("Time of day", list(TIME_BUCKET_LABELS), key="cal_tod", label_visibility="collapsed")
    # Date options come from the unfiltered frame, so narrowing another filter
    # can't drop a date out from under the pickers.
    free_time = render_free_time_filter(wl_shows, key_prefix="cal")

    filters = AgendaFilters(
        search=search,
        theaters=tuple(sel_theaters),
        runtimes=tuple(sel_runtime),
        time_buckets=tuple(sel_tod),
        min_rating=min_rating,
        only_free=free_time.enabled,
        free_cutoff=free_time.cutoff,
        days_off=free_time.days_off,
        unavailable=free_time.unavailable,
    )
    return filters, sort_mode, view or VIEW_AGENDA, export_slot


def _render_export(filtered: pd.DataFrame) -> None:
    """Export popover: ICS as the primary download, CSV behind an expander.

    Reads the same frame the agenda renders, so the download always matches
    what's on screen — including the day-strip selection.
    """
    with st.popover("Export", icon=":material/download:", use_container_width=True):
        st.download_button(
            "📅 .ics (Google / Apple / Outlook)",
            data=to_ics(build_ics_events(filtered)),
            file_name="watchlist_calendar.ics",
            mime="text/calendar",
            use_container_width=True,
        )
        with st.expander("CSV (legacy Google Calendar import)"):
            csv_rows = build_csv_rows(filtered)
            if csv_rows:
                st.download_button(
                    "Download CSV",
                    data=pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8"),
                    file_name="watchlist_calendar.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


def _render_map(filtered: pd.DataFrame, theaters_csv: object) -> None:
    """Theater map for the filtered screenings; marker size ∝ screening count."""
    if not theaters_csv:
        st.info("Set `ALLOCINE_INPUT_PATH` in `.env` to render the theater map.")
        return
    try:
        geo = load_geocoded_theaters(str(theaters_csv))
    except Exception as exc:
        st.warning(f"Geocoding unavailable: {exc}")
        return
    # Built column-wise rather than via ``.size().rename("count").reset_index()``:
    # ``DataFrameGroupBy.size`` is typed ``DataFrame | Series``, and ``rename`` on the
    # DataFrame arm takes no positional string, so the chained form matches no overload.
    if "theater_id" in filtered.columns:
        sizes = filtered.groupby("theater_id").size()
        counts = pd.DataFrame({"theater_id": sizes.index, "count": sizes.to_numpy()})
    else:
        counts = pd.DataFrame(columns=["theater_id", "count"])
    merged = geo.merge(counts, left_on="id", right_on="theater_id", how="left").fillna({"count": 0})
    merged = merged[merged["count"] > 0]
    render_theater_map(merged, count_col="count", popup_col="name")


def main() -> None:
    movies_path, showtimes_path, theaters_csv = get_paths()

    col_title, col_fresh = st.columns([3, 1], vertical_alignment="center")
    with col_title:
        st.markdown('<h1 class="h-display" style="font-size:2rem;">Watchlist Screenings</h1>', unsafe_allow_html=True)
        st.caption("Upcoming screenings of your Letterboxd watchlist movies across your configured theaters.")
    with col_fresh:
        render_freshness_banner(
            showtimes_path,
            (movies_path / "watchlist_with_letterboxd.parquet") if movies_path else None,
        )

    if not movies_path:
        st.error("**OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists() or not showtimes_path.exists():
        render_empty_state(
            "📥",
            "Data missing",
            "Run the orchestrate.py CLI to scrape watchlist + showtimes.",
        )
        return

    try:
        watchlist_df = load_watchlist(str(movies_path))
        showtimes_df = load_showtimes(str(showtimes_path))
        ratings_df = (
            load_ratings(str(movies_path)) if (movies_path / "ratings_with_letterboxd.parquet").exists() else pd.DataFrame()
        )
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    showtimes_df = future_showtimes(showtimes_df)
    wl_shows = build_watchlist_showtimes(showtimes_df, watchlist_df)
    if wl_shows.empty:
        render_empty_state(
            "🍿",
            "No upcoming watchlist screenings",
            "None of your watchlist films are currently showing. Showtimes refresh Tuesday morning.",
        )
        return

    # Scored before any filtering: `match` then survives every narrowing for
    # free, and attach_match also carries back the metadata columns the
    # "because" chips need (see core.taste). Note it resets the index, so
    # nothing index-aligned may cross this line.
    profile: TasteProfile | None = build_affinity(ratings_df) if not ratings_df.empty else None
    has_profile = profile is not None and not profile.is_empty
    if has_profile and profile is not None:
        wl_shows = attach_match(wl_shows, watchlist_df, profile)

    # Filled after filtering, so both describe the frame the agenda shows — see
    # the day-strip note below.
    kpi_slot = st.container()
    day_slot = st.container()

    filters, sort_mode, view, export_slot = _render_toolbar(wl_shows, has_profile=has_profile)

    narrowed = apply_filters(wl_shows, filters)
    if narrowed.empty:
        render_empty_state("🔍", "No matches", "Loosen the filters to see more screenings.")
        return

    with kpi_slot:
        render_kpi_strip(agenda_kpis(narrowed))
    # The day chips count the *filtered* frame, which is why they render from a
    # slot placed above the toolbar but filled after it. KPIs and chips both
    # describe `narrowed` (pre-day), so picking a day re-scopes the agenda and
    # the export without zeroing the headline counts.
    with day_slot:
        st.write("")  # breathing room between the KPI cards and the day chips
        day = render_day_strip(day_chips(narrowed), key="cal_day")

    filtered = apply_day(narrowed, day)
    with export_slot:
        _render_export(filtered)

    if filtered.empty:
        render_empty_state("🔍", "Nothing on that day", "Pick another day, or go back to All.")
        return

    if view == VIEW_MAP:
        _render_map(filtered, theaters_csv)
    else:
        render_agenda(build_agenda(filtered, sort=sort_mode), profile=profile if has_profile else None)


main()
