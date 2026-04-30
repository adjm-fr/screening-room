"""
Watchlist Calendar page.

Cross-joins:
- watchlist_with_letterboxd.parquet (from movies_management) — what you want to see
- showtimes.parquet (from Allocine-Showtimes-Scraping) — what is showing

Displays upcoming showtimes for watchlist movies as an interactive calendar
(streamlit-calendar) with a fallback table when the package is unavailable.
Also offers a Google Calendar CSV download.
"""

import pandas as pd
import streamlit as st

from utils.data_loader import build_watchlist_showtimes, future_showtimes, get_paths, load_showtimes, load_watchlist


def _to_calendar_events(df: pd.DataFrame) -> list[dict]:
    events = []
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

        events.append(
            {
                "title": row["movie"],
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "color": "#e63946",
                "extendedProps": {"theater": theater},
            }
        )
    return events


def main() -> None:
    st.title("Watchlist Calendar")
    st.markdown("Shows upcoming screenings of your Letterboxd watchlist movies across your configured theaters.")

    movies_path, showtimes_path, _ = get_paths()

    if not movies_path:
        st.error("**MOVIES_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return

    watchlist_file = movies_path / "watchlist_with_letterboxd.parquet"
    if not watchlist_file.exists():
        st.warning("Watchlist data not found. Run `python main.py` in `movies_management` first.")
        return
    if not showtimes_path.exists():
        st.warning("Showtimes data not found. Run `python main.py` in the `Allocine-Showtimes-Scraping` project first.")
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
        st.info("No upcoming showtimes found for your watchlist movies.")
        return

    # ── Sidebar filters ───────────────────────────────────────────────────────
    st.sidebar.header("Calendar filters")

    if "theater_name" in wl_shows.columns:
        all_theaters = sorted(wl_shows["theater_name"].dropna().unique())
        selected_theaters = st.sidebar.multiselect("Theaters", all_theaters, default=all_theaters)
        wl_shows = wl_shows[wl_shows["theater_name"].isin(selected_theaters)]

    if not wl_shows.empty:
        min_dt = pd.to_datetime(wl_shows["showtimes"]).min()
        max_dt = pd.to_datetime(wl_shows["showtimes"]).max()

        date_range = st.sidebar.date_input(
            "Date range",
            value=(min_dt.date(), max_dt.date()),
            min_value=min_dt.date(),
            max_value=max_dt.date(),
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
            mask = (pd.to_datetime(wl_shows["showtimes"]).dt.date >= start_date) & (
                pd.to_datetime(wl_shows["showtimes"]).dt.date <= end_date
            )
            wl_shows = wl_shows[mask]

    # ── Summary ───────────────────────────────────────────────────────────────
    m1, m2 = st.columns(2)
    m1.metric("Watchlist movies with showtimes", wl_shows["movie"].nunique())
    m2.metric("Total upcoming screenings", len(wl_shows))

    st.divider()

    # ── Calendar view ─────────────────────────────────────────────────────────
    events = _to_calendar_events(wl_shows)

    try:
        from streamlit_calendar import calendar  # type: ignore

        calendar_options = {
            "initialView": "timeGridWeek",
            "timeZone": "Europe/Paris",
            "headerToolbar": {
                "left": "prev,next today",
                "center": "title",
                "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
            },
            "height": 650,
        }
        calendar(events=events, options=calendar_options)

    except ImportError:
        st.info("Install `streamlit-calendar` for an interactive calendar view. Showing table instead.")
        table_df = (
            wl_shows[["showtimes", "movie", "theater_name", "theater_id"]]
            .sort_values("showtimes")
            .rename(columns={"showtimes": "Date & Time", "movie": "Movie", "theater_name": "Theater"})
            .reset_index(drop=True)
        )
        st.dataframe(table_df, width="stretch")

    # ── Table view (always shown below calendar) ──────────────────────────────
    with st.expander("Show as table"):
        display_cols = [c for c in ["showtimes", "movie", "theater_name", "director", "runtime_minutes"] if c in wl_shows.columns]
        st.dataframe(wl_shows[display_cols].sort_values("showtimes").reset_index(drop=True), width="stretch")

    # ── Google Calendar CSV download ──────────────────────────────────────────
    st.divider()
    st.subheader("Export to Google Calendar")

    if wl_shows.empty:
        st.info("No watchlist showtimes to export.")
    else:
        try:

            def _sanitize(value: str) -> str:
                value = str(value).replace("\r", " ").replace("\n", " ")
                if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
                    value = "'" + value
                return value

            events = []
            for _, row in wl_shows.iterrows():
                showtime = pd.to_datetime(row["showtimes"])
                if pd.isna(showtime):
                    continue
                runtime = row.get("runtime_minutes")
                try:
                    runtime_min = int(float(runtime)) if runtime and not pd.isna(runtime) else 120
                except (ValueError, TypeError):
                    runtime_min = 120
                end_time = showtime + pd.Timedelta(minutes=runtime_min)
                theater = _sanitize(row.get("theater_name") or row.get("theater_id", ""))
                events.append(
                    {
                        "Subject": _sanitize(row["movie"]),
                        "Start Date": showtime.strftime("%Y-%m-%d"),
                        "Start Time": showtime.strftime("%H:%M:%S"),
                        "End Date": end_time.strftime("%Y-%m-%d"),
                        "End Time": end_time.strftime("%H:%M:%S"),
                        "All Day Event": "False",
                        "Description": f"Theater: {theater} | Director: {_sanitize(row.get('director') or 'N/A')}",
                        "Location": theater,
                        "Private": "False",
                    }
                )

            if events:
                csv_bytes = pd.DataFrame(events).to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="Download Calendar CSV",
                    data=csv_bytes,
                    file_name="watchlist_calendar.csv",
                    mime="text/csv",
                )
        except Exception as exc:
            st.error(f"Failed to generate calendar: {exc}")


main()
