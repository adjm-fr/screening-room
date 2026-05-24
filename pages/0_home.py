"""
Home — overview hub for the cinema dashboard.

Leads with the answer ("what to watch tonight") rather than KPIs. Built
around three poster rails (next-up, taste-driven, by-genre chips) and a
small KPI strip at the bottom for quick reference. Falls back to a designed
empty state when no upcoming watchlist screenings exist.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.config import settings
from utils.data_loader import (
    attach_streaming,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from utils.ui import (
    render_empty_state,
    render_freshness_banner,
    render_hero_card,
    render_kpi_strip,
    render_poster_rail,
)


def _eyebrow_for(when: pd.Timestamp) -> str:
    """Return a friendly eyebrow string ("Tonight 19:30", "Saturday 21 Mar 14:00")."""
    now = pd.Timestamp.now()
    if when.date() == now.date():
        return f"Tonight · {when.strftime('%H:%M')}"
    if when.date() == (now + pd.Timedelta(days=1)).date():
        return f"Tomorrow · {when.strftime('%H:%M')}"
    return when.strftime("%A %d %b · %H:%M")


def _top_director(ratings_df: pd.DataFrame) -> str | None:
    if "directors" not in ratings_df.columns or "user_rating" not in ratings_df.columns:
        return None
    exploded = (
        ratings_df[["directors", "user_rating"]]
        .dropna()
        .assign(director=lambda d: d["directors"].str.split(", "))
        .explode("director")
    )
    if exploded.empty:
        return None
    top = (
        exploded.groupby("director")["user_rating"]
        .agg(["mean", "count"])
        .query("count >= 2")
        .sort_values("mean", ascending=False)
    )
    return str(top.index[0]) if not top.empty else None


def _films_by_director(wl_shows: pd.DataFrame, director: str) -> pd.DataFrame:
    if "directors" not in wl_shows.columns:
        return wl_shows.iloc[0:0]
    return wl_shows[wl_shows["directors"].fillna("").str.contains(director, case=False, regex=False)]


def main() -> None:
    movies_path, showtimes_path, _ = get_paths()

    st.markdown('<h1 class="h-display" style="font-size:2.4rem;">Cinema Dashboard</h1>', unsafe_allow_html=True)
    st.caption("Your watchlist, your theaters, your taste — in one screen.")

    showtimes_file = showtimes_path if showtimes_path else None
    watchlist_file = (movies_path / "watchlist_with_letterboxd.parquet") if movies_path else None
    render_freshness_banner(showtimes_file, watchlist_file)

    if not movies_path or not showtimes_path:
        render_empty_state(
            "⚙️",
            "Configure your data paths",
            "Set MOVIES_OUTPUT_PATH and ALLOCINE_OUTPUT_PATH in .env to populate the dashboard.",
        )
        return
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists() or not showtimes_path.exists():
        render_empty_state(
            "🎬",
            "No data yet",
            "Run the orchestrate.py CLI (or Dagster) to scrape watchlist + showtimes.",
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
    wl_shows = build_watchlist_showtimes(showtimes_df, watchlist_df).sort_values("showtimes").reset_index(drop=True)
    wl_shows = attach_streaming(wl_shows, str(movies_path))
    subscribed = settings.streaming_service_slugs

    if wl_shows.empty:
        render_empty_state(
            "🍿",
            "No watchlist screenings coming up",
            "Showtimes refresh Tuesday morning — check back, or browse all upcoming films.",
            cta=("Browse watchlist screenings →", "pages/calendar.py"),
        )
        return

    # ── Hero: tonight's pick ─────────────────────────────────────────────────
    next_screening = wl_shows.iloc[0]
    eyebrow = _eyebrow_for(pd.to_datetime(next_screening["showtimes"]))
    render_hero_card(next_screening, eyebrow=eyebrow, subscribed=subscribed)
    st.write("")

    # ── Up next rail ─────────────────────────────────────────────────────────
    up_next = wl_shows.iloc[1:9]
    render_poster_rail(up_next, title="Up next on your watchlist", subscribed=subscribed)

    # ── Because you liked X ──────────────────────────────────────────────────
    if not ratings_df.empty:
        top_director = _top_director(ratings_df)
        if top_director:
            director_films = _films_by_director(wl_shows, top_director)
            if not director_films.empty:
                deduped = director_films.drop_duplicates(subset=["letterboxd_title"]).head(6)
                render_poster_rail(deduped, title=f"Because you like {top_director}", subscribed=subscribed)

    # ── Discover by genre chips ──────────────────────────────────────────────
    if "genres" in wl_shows.columns:
        all_genres: list[str] = (
            wl_shows["genres"]
            .dropna()
            .str.split(", ")
            .explode()
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .value_counts()
            .head(12)
            .index.tolist()
        )
        if all_genres:
            st.markdown("##### Discover by genre")
            picked = st.pills(
                "genre filter",
                options=all_genres,
                selection_mode="single",
                key="home_genre",
                label_visibility="collapsed",
            )
            if picked:
                filtered = wl_shows[wl_shows["genres"].fillna("").str.contains(picked, case=False, regex=False)]
                deduped = filtered.drop_duplicates(subset=["letterboxd_title"]).head(8)
                render_poster_rail(deduped, title=f"{picked} on your watchlist", subscribed=subscribed)

    # ── KPI strip at the bottom ──────────────────────────────────────────────
    st.divider()
    n_rated = len(ratings_df) if not ratings_df.empty else 0
    n_watchlist = len(watchlist_df)
    n_screenings = len(wl_shows)
    has_theater_col = not showtimes_df.empty and "theater_name" in showtimes_df.columns
    n_theaters = showtimes_df["theater_name"].nunique() if has_theater_col else 0
    render_kpi_strip(
        [
            ("Films rated", n_rated),
            ("Watchlist size", n_watchlist),
            ("Upcoming screenings", n_screenings),
            ("Theaters tracked", n_theaters),
        ]
    )


main()
