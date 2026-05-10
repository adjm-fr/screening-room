"""
Watchlist Screenings page.

Joins ``watchlist_with_letterboxd.parquet`` with ``showtimes.parquet`` and
displays upcoming screenings across three surfaces:

- **By day** — horizontal poster rails grouped by date, all cards fully
  populated (inner-join means every card has a poster and Letterboxd data).
- **Calendar** — ``streamlit-calendar`` widget with events colored along an
  amber heatmap by Letterboxd rating (gold = high score, faded = low),
  always paired with a numeric rating in the title for accessibility.
- **Map** — pydeck map of theaters carrying screenings in the current
  filter; marker size ∝ # of watchlist screenings.

Top chip-filter bar holds theaters, genres, runtime buckets, weekend toggle,
and a text search; the sidebar carries only the heavy date-range picker.
ICS export is the primary download (universally accepted by Google Calendar /
Apple Calendar / Outlook); CSV is kept behind an expander for legacy use.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.data_loader import build_watchlist_showtimes, future_showtimes, get_paths, load_showtimes, load_watchlist
from utils.geo import load_geocoded_theaters, render_theater_map
from utils.ui import (
    rating_to_hsl,
    render_chip_filter,
    render_empty_state,
    render_poster_rail,
    to_ics,
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


def _to_calendar_events(df: pd.DataFrame) -> list[dict]:
    """Build streamlit-calendar event dicts with rating-heatmap colors."""
    events: list[dict] = []
    for _, row in df.iterrows():
        start_dt = pd.to_datetime(row["showtimes"])
        if pd.isna(start_dt):
            continue
        runtime = row.get("runtime_minutes")
        try:
            duration = int(float(runtime)) if runtime and not pd.isna(runtime) else 120
        except (ValueError, TypeError):
            duration = 120
        end_dt = start_dt + pd.Timedelta(minutes=duration)
        theater = row.get("theater_name") or row.get("theater_id", "")
        rating = row.get("letterboxd_avg_rating")
        rating_label = f" · ★ {float(rating):.1f}" if isinstance(rating, (int, float)) and not pd.isna(rating) else ""
        events.append(
            {
                "title": f"{row['french_title']}{rating_label}",
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "color": rating_to_hsl(rating if isinstance(rating, (int, float)) else None) if rating_label else "#7f7f7f",
                "extendedProps": {"theater": str(theater), "rating": rating_label.strip(" ·")},
            }
        )
    return events


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
        st.error("**MOVIES_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
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

    # ── Filter bar row 1: chips ──────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        theaters = sorted(wl_shows["theater_name"].dropna().unique().tolist()) if "theater_name" in wl_shows.columns else []
        sel_theaters = render_chip_filter("Theaters", theaters, key="cal_theaters", default=theaters)
    with fc2:
        genres_set: set[str] = set()
        if "genres" in wl_shows.columns:
            for g in wl_shows["genres"].dropna():
                genres_set.update(p.strip() for p in str(g).split(",") if p.strip())
        sel_genres = render_chip_filter("Genres", sorted(genres_set), key="cal_genres")
    with fc3:
        sel_runtime = render_chip_filter("Runtime", ["<90", "90–120", ">120"], key="cal_runtime")

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
    if sel_genres and "genres" in filtered.columns:
        pattern = "|".join(g.replace("|", r"\|") for g in sel_genres)
        filtered = filtered[filtered["genres"].fillna("").str.contains(pattern, case=False, regex=True)]
    if sel_runtime:
        filtered = filtered[filtered["_runtime_bucket"].isin(sel_runtime)]
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
        filtered_sorted = filtered.sort_values("showtimes")
        days = filtered_sorted.assign(_day=pd.to_datetime(filtered_sorted["showtimes"]).dt.date).groupby("_day", sort=True)
        for day, group in days:
            day_label = pd.Timestamp(str(day)).strftime("%A %d %B")
            deduped = group.drop_duplicates(subset=["letterboxd_title", "theater_name"]).head(20)
            render_poster_rail(deduped, title=day_label)

    with tab_cal:
        try:
            from streamlit_calendar import calendar  # type: ignore[import-untyped]
        except ImportError:
            st.info("Install `streamlit-calendar` for the calendar view.")
        else:
            events = _to_calendar_events(filtered)
            calendar(
                events=events,
                options={
                    "initialView": "timeGridWeek",
                    "timeZone": "Europe/Paris",
                    "headerToolbar": {
                        "left": "prev,next today",
                        "center": "title",
                        "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
                    },
                    "height": 650,
                },
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

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
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


main()
