"""
Home — overview hub for the cinema dashboard.

Leads with the answer ("what to watch tonight") rather than KPIs. Built
around three poster rails (next-up, streaming, taste-ranked top matches)
and a small KPI strip at the bottom for quick reference. Falls back to a
designed empty state when no upcoming watchlist screenings exist.
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
from utils.taste import attach_match, build_affinity
from utils.ui import (
    match_chips_html,
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
            "Set OUTPUT_PATH and ALLOCINE_OUTPUT_PATH in .env to populate the dashboard.",
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
    profile = build_affinity(ratings_df) if not ratings_df.empty else None

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

    # ── Screening next rail ──────────────────────────────────────────────────
    up_next = wl_shows.iloc[1:9]
    render_poster_rail(up_next, title="Screening next on your watchlist", subscribed=subscribed)

    # ── Available on streaming platforms ─────────────────────────────────────
    # When STREAMING_SERVICES is unset, fall back to "any provider" so the rail
    # is still useful before the user configures their subscriptions.
    wl_streaming = attach_streaming(watchlist_df, str(movies_path))
    if subscribed:
        wl_streaming = wl_streaming[wl_streaming["flatrate"].apply(lambda f: bool(set(f) & subscribed))]
    else:
        wl_streaming = wl_streaming[wl_streaming["flatrate"].apply(lambda f: len(f) > 0)]
    if not wl_streaming.empty:
        # Rank by taste match when a profile exists; community rating breaks
        # ties and is the fallback ordering before any films are rated.
        if profile is not None and not profile.is_empty:
            wl_streaming = attach_match(wl_streaming, watchlist_df, profile)
            wl_streaming = wl_streaming.sort_values(["match", "letterboxd_avg_rating"], ascending=False, na_position="last")
        else:
            wl_streaming = wl_streaming.sort_values("letterboxd_avg_rating", ascending=False, na_position="last")
        wl_streaming = wl_streaming.drop_duplicates(subset=["tmdb_id"]).head(8)
        render_poster_rail(wl_streaming, title="Available on streaming platforms", subscribed=subscribed)

    # ── Top matches this week ────────────────────────────────────────────────
    # Taste-ranked rail over this week's watchlist screenings (see utils.taste):
    # % badge + "because" chips name the actual contributors, so the rail has
    # content every week — unlike a single-director coincidence gate.
    if profile is not None and not profile.is_empty:
        top = attach_match(wl_shows, watchlist_df, profile)
        top = (
            top.dropna(subset=["match"])
            .sort_values("match", ascending=False)
            .drop_duplicates(subset=["letterboxd_title"])
            .head(8)
        )
        if not top.empty:
            render_poster_rail(
                top,
                title="Top matches this week",
                subscribed=subscribed,
                extra_html_fn=lambda r: match_chips_html(r, profile),
            )

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
