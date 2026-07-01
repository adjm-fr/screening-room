"""
Watchlist Screenings page.

Joins ``watchlist_with_letterboxd.parquet`` with ``showtimes.parquet`` and
displays upcoming screenings across three surfaces:

- **By day** — horizontal poster rails grouped by date, one card per movie
  with all showtimes for that day listed below, sorted by earliest showtime.
- **Calendar** — ICS and CSV export for your watchlist screenings.
- **Map** — pydeck map of theaters carrying screenings in the current
  filter; marker size ∝ # of watchlist screenings.

Top filter bar holds a theater multi-select dropdown (a growing theater list
made chip-toggles unwieldy), runtime buckets, a showtime time-of-day range,
weekend toggle, and a text search; the sidebar carries only the heavy
date-range picker. All of these filters (including the time-of-day range)
narrow the same ``filtered`` frame that both the day rails and the ICS/CSV
export read from, so exports always match what's on screen.
ICS export is the primary download (universally accepted by Google Calendar /
Apple Calendar / Outlook); CSV is kept behind an expander for legacy use.
"""

from __future__ import annotations

import datetime as dt
import html as _html

import pandas as pd
import streamlit as st
from utils.data_loader import (
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_showtimes,
    load_watchlist,
)
from utils.geo import load_geocoded_theaters, render_theater_map
from utils.ui import (
    _movie_card_html,
    render_chip_filter,
    render_empty_state,
    to_ics,
)


def _render_day_rails(
    rows: pd.DataFrame,
    *,
    title_col: str,
) -> None:
    """Render the by-day poster rails for a (pre-sorted) subset of screenings."""
    for day, day_group in rows.groupby("_day", sort=True):
        day_label = pd.Timestamp(str(day)).strftime("%A %d %B")
        cards_html = ""
        for _, movie_group in day_group.groupby(title_col, sort=False):
            rep = movie_group.iloc[0]
            showtimes_lines = ""
            for _, st_row in movie_group.sort_values("_dt").iterrows():
                t = st_row["_dt"].strftime("%H:%M")
                theater = str(st_row.get("theater_name") or "")
                line = _html.escape(t)
                if theater:
                    line += f" · {_html.escape(theater)}"
                showtimes_lines += f'<div class="showtime-badge">{line}</div>'
            cards_html += _movie_card_html(rep, extra_html=showtimes_lines)
        st.markdown(
            f'<div class="poster-rail-wrap">'
            f'<div class="poster-rail-title">{_html.escape(day_label)}</div>'
            f'<div class="poster-rail">{cards_html}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )


def _runtime_bucket(minutes: float | str | None) -> str:
    if minutes is None or (isinstance(minutes, float) and pd.isna(minutes)):
        return "Unknown"
    if isinstance(minutes, str):
        minutes = minutes.strip()
        if not minutes:
            return "Unknown"
        try:
            m = int(float("".join(c for c in minutes if c.isdigit() or c == ".")))
        except (ValueError, TypeError):
            return "Unknown"
    else:
        try:
            m = int(minutes)
        except (ValueError, TypeError):
            return "Unknown"
    if m < 90:
        return "<90"
    if m <= 120:
        return "90–120"
    return ">120"


def _build_ics_events(df: pd.DataFrame) -> list[dict]:
    events: list[dict] = []
    for idx, row in df.iterrows():
        showtime = pd.to_datetime(row["showtimes"])
        if pd.isna(showtime):
            continue
        runtime = row.get("runtime_minutes")
        try:
            runtime_min = int(float(runtime)) if runtime and not pd.isna(runtime) else 120
        except (ValueError, TypeError):
            runtime_min = 120
        end = showtime + pd.Timedelta(minutes=runtime_min)
        events.append(
            {
                "summary": str(row.get("letterboxd_title") or row["french_title"]),
                "start": showtime,
                "end": end,
                "location": str(row.get("theater_name") or row.get("theater_id", "")),
                "description": f"Directors: {row.get('directors') or 'N/A'} | French title: {row['french_title']}",
                "uid": f"{idx}-{int(showtime.timestamp())}@cinema_dashboard",
            }
        )
    return events


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2rem;">Watchlist Screenings</h1>', unsafe_allow_html=True)
    st.caption("Upcoming screenings of your Letterboxd watchlist movies across your configured theaters.")

    movies_path, showtimes_path, theaters_csv = get_paths()
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

    runtime_col = next((c for c in ("runtime_minutes", "runtime") if c in wl_shows.columns), None)
    wl_shows = wl_shows.copy()
    wl_shows["_runtime_bucket"] = wl_shows[runtime_col].apply(_runtime_bucket) if runtime_col else "Unknown"

    # ── Filter bar row 1: theater dropdown + runtime chips + time range ─────
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        theaters = sorted(wl_shows["theater_name"].dropna().unique().tolist()) if "theater_name" in wl_shows.columns else []
        sel_theaters = st.multiselect("Theaters", theaters, default=theaters, key="cal_theaters")
    with fc2:
        sel_runtime = render_chip_filter("Runtime", ["<90", "90–120", ">120"], key="cal_runtime")
    with fc3:
        sel_time_range = st.slider(
            "Showtime between",
            min_value=dt.time(0, 0),
            max_value=dt.time(23, 59),
            value=(dt.time(0, 0), dt.time(23, 59)),
            step=dt.timedelta(minutes=15),
            key="cal_timerange",
        )

    # ── Filter bar row 2: toggle + search ───────────────────────────────────
    fr1, fr2 = st.columns([1, 3])
    with fr1:
        weekend_only = st.toggle("Weekends only", value=False, key="cal_weekend")
    with fr2:
        search = st.text_input("Search title or director", key="cal_search", placeholder="e.g. Bong Joon-ho")

    # ── Sidebar: date range + min rating ────────────────────────────────────
    st.sidebar.header("Filters")
    min_dt = pd.to_datetime(wl_shows["showtimes"]).min()
    max_dt = pd.to_datetime(wl_shows["showtimes"]).max()
    date_range = st.sidebar.date_input(
        "Show screenings between",
        value=(min_dt.date(), max_dt.date()),
        min_value=min_dt.date(),
        max_value=max_dt.date(),
    )
    min_rating = st.sidebar.slider("Min Letterboxd rating", 0.0, 10.0, 0.0, 0.5, key="cal_minrating")

    filtered = wl_shows.copy()
    if sel_theaters:
        filtered = filtered[filtered["theater_name"].isin(sel_theaters)]
    if sel_runtime:
        filtered = filtered[filtered["_runtime_bucket"].isin(sel_runtime)]
    if sel_time_range and sel_time_range != (dt.time(0, 0), dt.time(23, 59)):
        start_t, end_t = sel_time_range
        showtime_of_day = pd.to_datetime(filtered["showtimes"]).dt.time
        filtered = filtered[(showtime_of_day >= start_t) & (showtime_of_day <= end_t)]
    if weekend_only:
        filtered = filtered[pd.to_datetime(filtered["showtimes"]).dt.dayofweek >= 5]
    if search:
        s_norm = search.lower().strip()
        title_col = "french_title" if "french_title" in filtered.columns else "movie"
        mask = filtered[title_col].fillna("").str.lower().str.contains(s_norm, regex=False)
        if "directors" in filtered.columns:
            mask = mask | filtered["directors"].fillna("").str.lower().str.contains(s_norm, regex=False)
        filtered = filtered[mask]
    if min_rating > 0 and "letterboxd_avg_rating" in filtered.columns:
        filtered = filtered[filtered["letterboxd_avg_rating"].fillna(0) >= min_rating]
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        mask = (pd.to_datetime(filtered["showtimes"]).dt.date >= start_date) & (
            pd.to_datetime(filtered["showtimes"]).dt.date <= end_date
        )
        filtered = filtered[mask]

    m1, m2 = st.columns(2)
    m1.metric("Watchlist movies", filtered["french_title"].nunique() if not filtered.empty else 0)
    m2.metric("Total screenings", len(filtered))

    if filtered.empty:
        render_empty_state("🔍", "No matches", "Loosen the filters to see more screenings.")
        return

    tab_days, tab_cal, tab_map = st.tabs(["🎬 By day", "📅 Calendar", "🗺️ Map"])

    with tab_days:
        title_col = "french_title" if "french_title" in filtered.columns else "movie"
        filtered_sorted = filtered.copy()
        filtered_sorted["_dt"] = pd.to_datetime(filtered_sorted["showtimes"])
        filtered_sorted["_day"] = filtered_sorted["_dt"].dt.date
        # Sort movies within each day by earliest showtime
        earliest = filtered_sorted.groupby([title_col, "_day"])["_dt"].min().rename("_earliest")
        filtered_sorted = filtered_sorted.join(earliest, on=[title_col, "_day"])
        filtered_sorted = filtered_sorted.sort_values(["_day", "_earliest", "_dt"])

        st.markdown("### Cinema-only this week")
        st.caption("Showtimes for your watchlist films screening in Paris.")
        _render_day_rails(filtered_sorted, title_col=title_col)

    with tab_cal:
        st.subheader("Export")
        ics_bytes = to_ics(_build_ics_events(filtered))
        st.download_button(
            "📅 Download .ics (Google / Apple / Outlook)",
            data=ics_bytes,
            file_name="watchlist_calendar.ics",
            mime="text/calendar",
        )
        with st.expander("CSV (legacy Google Calendar import)"):
            csv_rows: list[dict] = []
            for _, row in filtered.iterrows():
                showtime = pd.to_datetime(row["showtimes"])
                if pd.isna(showtime):
                    continue
                runtime = row.get("runtime_minutes")
                try:
                    runtime_min = int(float(runtime)) if runtime and not pd.isna(runtime) else 120
                except (ValueError, TypeError):
                    runtime_min = 120
                end_time = showtime + pd.Timedelta(minutes=runtime_min)
                csv_rows.append(
                    {
                        "Subject": str(row.get("letterboxd_title") or row["french_title"]),
                        "Start Date": showtime.strftime("%Y-%m-%d"),
                        "Start Time": showtime.strftime("%H:%M:%S"),
                        "End Date": end_time.strftime("%Y-%m-%d"),
                        "End Time": end_time.strftime("%H:%M:%S"),
                        "All Day Event": "False",
                        "Description": f"Directors: {row.get('directors') or 'N/A'} | French title: {row['french_title']}",
                        "Location": str(row.get("theater_name") or row.get("theater_id", "")),
                        "Private": "False",
                    }
                )
            if csv_rows:
                csv_bytes = pd.DataFrame(csv_rows).to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Download CSV",
                    data=csv_bytes,
                    file_name="watchlist_calendar.csv",
                    mime="text/csv",
                )

    with tab_map:
        if not theaters_csv:
            st.info("Set `ALLOCINE_INPUT_PATH` in `.env` to render the theater map.")
        else:
            try:
                geo = load_geocoded_theaters(str(theaters_csv))
            except Exception as exc:
                st.warning(f"Geocoding unavailable: {exc}")
            else:
                counts = (
                    filtered.groupby("theater_id").size().rename("count").reset_index()
                    if "theater_id" in filtered.columns
                    else pd.DataFrame(columns=["theater_id", "count"])
                )
                merged = geo.merge(counts, left_on="id", right_on="theater_id", how="left").fillna({"count": 0})
                merged = merged[merged["count"] > 0]
                render_theater_map(merged, count_col="count", popup_col="name")


main()
