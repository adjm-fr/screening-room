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

from config import settings
from core.taste import TasteProfile, attach_match, build_affinity
from sources.loader import (
    attach_streaming,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from ui import (
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


#: How many cards the "Available on streaming platforms" rail shows.
STREAMING_RAIL_SIZE = 8


def _streaming_rail_frame(
    watchlist_df: pd.DataFrame,
    movies_output: str,
    *,
    subscribed: set[str] | frozenset[str],
    profile: TasteProfile | None,
) -> pd.DataFrame:
    """Build the ranked, capped frame for the "Available on streaming platforms" rail.

    The whole rail is assembled here rather than inline in :func:`main` so it is
    a single testable unit: the display-title rename below is invisible on a
    rendered card, so a call site that skipped it could regress silently.

    ``watchlist_df`` carries ``title`` (and often ``french_title``) but not
    ``letterboxd_title``. ``ui.cards._movie_card_html`` resolves the display
    title in ``letterboxd_title`` → ``french_title`` → ``title`` → ``movie``
    order, so without the rename cards on this rail fall through to the French
    title while every other surface in the app shows the canonical Letterboxd
    title. Mirrors the same rename already applied in ``pages/streaming.py``,
    ``pages/database.py``, and ``chat/ui.py``.

    A film is "available" when it is on a ``subscribed`` flatrate provider or on
    any no-cost ``free`` one — free platforms are watchable by everyone, so they
    are never gated by ``STREAMING_SERVICES``. When ``subscribed`` is empty the
    flatrate side falls back to "any provider" so the rail is still useful
    before subscriptions are configured. Ranking is by taste ``match`` when a
    profile exists (community rating breaking ties) and by community rating
    alone otherwise. Returns at most :data:`STREAMING_RAIL_SIZE` rows, one per
    ``tmdb_id``; an empty frame means "render no rail".

    ``attach_match`` is deliberately passed the *original* ``watchlist_df``
    rather than the renamed frame: it scores on metadata columns only and never
    reads a title, so this keeps the scoring input identical to every other
    ``attach_match`` call site.
    """
    frame = watchlist_df
    if "title" in frame.columns and "letterboxd_title" not in frame.columns:
        frame = frame.rename(columns={"title": "letterboxd_title"})
    out = attach_streaming(frame, movies_output)

    if subscribed:
        out = out[out.apply(lambda r: bool(set(r["flatrate"]) & subscribed) or bool(r["free"]), axis=1)]
    else:
        out = out[out.apply(lambda r: bool(r["flatrate"]) or bool(r["free"]), axis=1)]
    if out.empty:
        return out

    if profile is not None and not profile.is_empty:
        out = attach_match(out, watchlist_df, profile)
        out = out.sort_values(["match", "letterboxd_avg_rating"], ascending=False, na_position="last")
    else:
        out = out.sort_values("letterboxd_avg_rating", ascending=False, na_position="last")
    return out.drop_duplicates(subset=["tmdb_id"]).head(STREAMING_RAIL_SIZE)


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
    # One card per film (earliest screening wins — wl_shows is sorted by
    # showtime), and no streaming badges: this rail is purely about showtimes.
    up_next = wl_shows.drop_duplicates(subset=["letterboxd_title"]).iloc[1:9]
    render_poster_rail(up_next, title="Screening next on your watchlist")

    # ── Available on streaming platforms ─────────────────────────────────────
    # Selection, ranking and the display-title rename all live in
    # _streaming_rail_frame so the rail is one tested unit — see its docstring.
    wl_streaming = _streaming_rail_frame(watchlist_df, str(movies_path), subscribed=subscribed, profile=profile)
    if not wl_streaming.empty:
        render_poster_rail(wl_streaming, title="Available on streaming platforms", subscribed=subscribed)

    # ── Top matches this week ────────────────────────────────────────────────
    # Taste-ranked rail over this week's watchlist screenings (see core.taste):
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
