"""
Showtimes viewer page.

Reads ``showtimes.parquet`` produced by ``Allocine-Showtimes-Scraping`` and
displays upcoming screenings across three surfaces:

- **By day** — horizontal poster rails grouped by date, with posters
  resolved via a left-join to the watchlist on normalised title.
- **Map** — pydeck map of theaters with marker size ∝ today's showtime count.
- **Table** — raw dataframe with poster + Letterboxd link columns.

Top chip-filter bar (theaters, genres, runtime buckets, weekend toggle, search)
keeps interaction in the main flow rather than a sidebar.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.data_loader import (
    _normalize_title,
    future_showtimes,
    get_paths,
    load_showtimes,
    load_watchlist,
)
from utils.geo import load_geocoded_theaters, render_theater_map
from utils.ui import render_chip_filter, render_empty_state, render_poster_rail


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


def _enrich_with_watchlist(showtimes_df: pd.DataFrame, watchlist_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join showtimes with watchlist metadata on normalised title.

    Unlike :func:`build_watchlist_showtimes` (an inner join with director
    confirmation), this preserves every showtime row and just attaches
    ``poster_url``, ``letterboxd_title``, ``letterboxd_avg_rating``, ``genres``
    when a watchlist match exists, so the day grid can display posters when
    we have them and generic cards otherwise.
    """
    showtimes_df = showtimes_df.copy()
    showtimes_df["_key"] = showtimes_df["movie"].map(_normalize_title)

    candidate = ("title", "french_title", "poster_url", "letterboxd_avg_rating", "genres", "directors")
    cols = [c for c in candidate if c in watchlist_df.columns]
    if not cols:
        return showtimes_df

    wl = watchlist_df[cols].copy()
    if "french_title" in wl.columns:
        wl["_key"] = wl["french_title"].fillna(wl.get("title", "")).map(_normalize_title)
    else:
        wl["_key"] = wl["title"].map(_normalize_title)
    wl = wl.rename(columns={"title": "letterboxd_title"}).drop_duplicates(subset=["_key"])

    merged = showtimes_df.merge(wl.drop(columns=[c for c in ("french_title",) if c in wl.columns]), on="_key", how="left")
    return merged.drop(columns=["_key"])


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2rem;">Showtimes</h1>', unsafe_allow_html=True)
    st.caption("Upcoming screenings scraped from Allocine.")

    _, showtimes_path, theaters_csv = get_paths()
    movies_path, _, _ = get_paths()
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    if not showtimes_path.exists():
        render_empty_state(
            "🎬",
            "No showtimes data yet",
            "Run `python main.py` in the `Allocine-Showtimes-Scraping` project first.",
        )
        return

    try:
        df = load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load showtimes: {exc}")
        return

    df = future_showtimes(df).copy()
    if df.empty:
        render_empty_state("📭", "No upcoming showtimes", "All scraped showtimes have passed.")
        return

    if movies_path and (movies_path / "watchlist_with_letterboxd.parquet").exists():
        try:
            watchlist_df = load_watchlist(str(movies_path))
            df = _enrich_with_watchlist(df, watchlist_df)
        except Exception:
            pass

    df["runtime_bucket"] = df.get("runtime", pd.Series([None] * len(df))).apply(_runtime_bucket)

    # ── Top chip filter bar ──────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns([2, 2, 2])
    with fc1:
        theaters = sorted(df["theater_name"].dropna().unique().tolist()) if "theater_name" in df.columns else []
        sel_theaters = render_chip_filter("Theaters", theaters, key="st_theaters")
    with fc2:
        genres_set: set[str] = set()
        if "genres" in df.columns:
            for g in df["genres"].dropna():
                genres_set.update(p.strip() for p in str(g).split(",") if p.strip())
        sel_genres = render_chip_filter("Genres", sorted(genres_set), key="st_genres")
    with fc3:
        sel_runtime = render_chip_filter("Runtime", ["<90", "90–120", ">120"], key="st_runtime")

    fc4, fc5 = st.columns([1, 3])
    with fc4:
        weekend_only = st.toggle("Weekends only", value=False, key="st_weekend")
    with fc5:
        search = st.text_input("Search title or director", key="st_search", placeholder="e.g. Bong Joon-ho")

    filtered = df.copy()
    if sel_theaters:
        filtered = filtered[filtered["theater_name"].isin(sel_theaters)]
    if sel_genres and "genres" in filtered.columns:
        pattern = "|".join(g.replace("|", r"\|") for g in sel_genres)
        filtered = filtered[filtered["genres"].fillna("").str.contains(pattern, case=False, regex=True)]
    if sel_runtime:
        filtered = filtered[filtered["runtime_bucket"].isin(sel_runtime)]
    if weekend_only and "is_weekend" in filtered.columns:
        filtered = filtered[filtered["is_weekend"]]
    if search:
        s_norm = search.lower().strip()
        mask = filtered["movie"].fillna("").str.lower().str.contains(s_norm, regex=False)
        if "director" in filtered.columns:
            mask = mask | filtered["director"].fillna("").str.lower().str.contains(s_norm, regex=False)
        filtered = filtered[mask]

    if filtered.empty:
        render_empty_state("🔍", "No matches", "Loosen the filters to see more screenings.")
        return

    tab_grid, tab_map, tab_table = st.tabs(["📅 By day", "🗺️ Map", "📋 Table"])

    with tab_grid:
        filtered = filtered.sort_values("showtimes")
        days = filtered.assign(_day=pd.to_datetime(filtered["showtimes"]).dt.date).groupby("_day", sort=True)
        for day, group in days:
            day_label = pd.Timestamp(str(day)).strftime("%A %d %B")
            deduped = group.drop_duplicates(subset=["movie", "theater_name"]).head(20)
            render_poster_rail(deduped, title=day_label)

    with tab_map:
        if not theaters_csv:
            st.info("Set `ALLOCINE_INPUT_PATH` in `.env` to render the theater map.")
        else:
            try:
                geo = load_geocoded_theaters(str(theaters_csv))
            except Exception as exc:
                st.warning(f"Geocoding unavailable: {exc}")
            else:
                today = pd.Timestamp.now().normalize()
                today_counts = (
                    filtered[pd.to_datetime(filtered["showtimes"]).dt.normalize() == today]
                    .groupby("theater_id")
                    .size()
                    .rename("count")
                    .reset_index()
                )
                geo_with_counts = geo.merge(today_counts, left_on="id", right_on="theater_id", how="left").fillna({"count": 0})
                render_theater_map(geo_with_counts, count_col="count", popup_col="name")

    with tab_table:
        candidate_cols = ("poster_url", "showtimes", "movie", "theater_name", "director", "runtime")
        display_cols = [c for c in candidate_cols if c in filtered.columns]
        st.dataframe(
            filtered[display_cols].sort_values("showtimes").reset_index(drop=True),
            width="stretch",
            hide_index=True,
            column_config={
                "poster_url": st.column_config.ImageColumn("Poster", width="small"),
                "showtimes": st.column_config.DatetimeColumn("Date & time", format="ddd D MMM HH:mm"),
                "movie": "Movie",
                "theater_name": "Theater",
                "director": "Director",
                "runtime": st.column_config.NumberColumn("Runtime (min)", format="%d"),
            },
        )


main()
