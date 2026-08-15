"""
Movies Database page.

Reorganises the Letterboxd cache + ratings + watchlist into five tabs. The
computation behind them lives in :mod:`core.library` (pure stats) and
:mod:`ui.stats` (pure HTML builders); this module only renders.

- **Overview** — the ratings breakdown: half-star histogram grouped under the
  tier-ladder headers, by-decade profile, the you-vs-Letterboxd disagreement
  table, the genre × avg-rating bar, and two small frequency bars (runtime
  buckets, most-watched genres) side by side.
- **Taste** — the affinity profile behind every match badge, one signed-bar
  block per dimension (same ``.contrib-*`` vocabulary as the movie detail
  page's score breakdown), liked/disliked judged tier-relatively.
- **Discover** — chip filters (genre, director, min Letterboxd/your-rating
  sliders) over a ranked poster rail of matching films.
- **Tables** — the three raw dataframes behind a search box + column presets,
  with poster + a "Details" link into the movie detail page +
  IMDB/TMDB/Letterboxd link columns for power users.
- **Unmatched** — Allocine screenings whose film couldn't be resolved to a
  Letterboxd slug during cache enrichment (``unresolved_allocine.parquet``),
  otherwise invisible to the rest of the dashboard.
"""

from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import streamlit as st

from config import settings
from core.library import (
    TABLE_PRESETS,
    decade_profile,
    delta_summary,
    explode_tags,
    filter_table,
    genre_counts,
    preset_columns,
    rating_disagreements,
    rating_histogram,
    runtime_bucket_counts,
)
from core.taste import WEIGHTS, AffinityEntry, build_affinity, dimension_profile
from sources.loader import (
    attach_streaming,
    build_unresolved_showtimes,
    get_paths,
    load_letterboxd_cache,
    load_ratings,
    load_showtimes,
    load_unresolved_allocine,
    load_watchlist,
)
from ui import (
    decade_profile_html,
    format_runtime,
    movie_href,
    rating_histogram_html,
    render_chip_filter,
    render_empty_state,
    render_freshness_banner,
    render_kpi_strip,
    render_poster_rail,
)
from ui.stats import SignedBarRow, affinity_dimension_html, frequency_bars_html

# Mirrors pages/movie.py's _DIMENSION_LABELS so the two .contrib-* surfaces
# name the taste dimensions identically.
_DIMENSION_LABELS = {
    "directors": "Directors",
    "genres": "Genres",
    "themes": "Themes",
    "cast": "Cast",
    "decade": "Decades",
    "country": "Countries",
    "language": "Languages",
}

# format_taste_profile requires ≥2 rated films for people (a single film says
# more about the film than the person); broad dimensions keep everything.
_MIN_COUNT_BY_DIM = {"directors": 2, "cast": 2}


def _streaming_label(row: pd.Series) -> str:
    """Build the ``streaming_on`` label for one row, e.g. ``netflix, arte-tv (free)``.

    ``row`` is expected to carry ``flatrate`` (already filtered by the caller
    to the user's subscribed set — see :func:`_with_streaming_column`) and
    ``free`` (unfiltered: free providers are watchable by everyone regardless
    of subscription). Flatrate names render unadorned; free providers are
    suffixed ``" (free)"`` so the distinction is legible in this plain-text
    column, which has no color or separate column to lean on.
    """
    flat = row.get("flatrate")
    free = row.get("free")
    flat_names = flat if isinstance(flat, list) else []
    free_names = free if isinstance(free, list) else []
    return ", ".join([*sorted(flat_names), *(f"{p} (free)" for p in sorted(free_names))])


def _with_streaming_column(
    df: pd.DataFrame,
    movies_output: str,
    subscribed: set[str] | frozenset[str],
) -> pd.DataFrame:
    """Append a ``streaming_on`` column: subscribed flatrate services plus every free platform, per film.

    Returns the df unchanged (sans column) when it lacks ``tmdb_id`` — the
    link table still renders. Free providers (Arte.tv, France.tv, …) appear
    regardless of ``subscribed`` (see :func:`_streaming_label`); flatrate
    providers are filtered down to the user's subscriptions first. The column
    is a comma-separated string (empty for unmatched rows) to avoid the
    ``float('nan') → "nan"`` rendering pitfall called out in ``CLAUDE.md``.
    """
    if "tmdb_id" not in df.columns:
        return df
    enriched = attach_streaming(df, movies_output).copy()
    enriched["flatrate"] = enriched["flatrate"].apply(
        lambda flat: [p for p in flat if p in subscribed] if isinstance(flat, list) else []
    )
    enriched["streaming_on"] = enriched.apply(_streaming_label, axis=1)
    return enriched.drop(columns=["flatrate", "free"], errors="ignore")


def _with_detail_url(df: pd.DataFrame) -> pd.DataFrame:
    """Prepend a ``detail_url`` column linking each row to its movie detail page.

    The value is the same relative ``?movie=<slug>`` href the cards use
    (:func:`ui.movie_href`), which ``st.column_config.LinkColumn`` opens
    against the current document — no base URL to configure. Rows without a
    slug get an empty cell, which the link column renders as blank. Returns the
    frame untouched when there is no ``slug`` column at all.
    """
    if "slug" not in df.columns:
        return df
    out = df.copy()
    out.insert(0, "detail_url", out["slug"].map(lambda s: movie_href(s) if isinstance(s, str) and s else ""))
    return out


def _genre_bubble_chart(ratings_df: pd.DataFrame) -> None:
    if "genres" not in ratings_df.columns or "user_rating" not in ratings_df.columns:
        st.info("No genres or ratings to plot.")
        return
    exploded = ratings_df[["genres", "user_rating"]].dropna().assign(genre=lambda d: d["genres"].str.split(", ")).explode("genre")
    exploded["genre"] = exploded["genre"].str.strip()
    exploded = exploded[exploded["genre"] != ""]
    summary = exploded.groupby("genre")["user_rating"].agg(["mean", "count"]).reset_index()
    summary = summary[summary["count"] >= 2].sort_values("mean", ascending=False).head(15)
    if summary.empty:
        st.info("Not enough rated films to summarise by genre yet.")
        return
    summary_sorted = summary.sort_values("mean")
    fig = px.bar(
        summary_sorted,
        x="mean",
        y="genre",
        orientation="h",
        color="mean",
        color_continuous_scale="oranges",
        text=summary_sorted["count"].astype(str) + " films",
        labels={"mean": "Avg rating", "genre": ""},
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(margin=dict(l=8, r=8, t=8, b=8), height=380, coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")


def _runtime_stats(ratings_df: pd.DataFrame) -> None:
    """P25/P50/P75 plus a compact 3-bucket runtime bar — a small CSS list, not a full-width chart."""
    if "runtime" not in ratings_df.columns:
        st.caption("No runtime data.")
        return
    runtimes = ratings_df["runtime"].dropna()
    if runtimes.empty:
        st.caption("No runtime data.")
        return
    p25, p50, p75 = (int(runtimes.quantile(q)) for q in (0.25, 0.5, 0.75))
    st.markdown(
        f"<div class='kpi-card'><div class='kpi-label'>P25/P50/P75</div>"
        f"<div class='kpi-value'>{format_runtime(p25)} · {format_runtime(p50)} · {format_runtime(p75)}</div></div>",
        unsafe_allow_html=True,
    )
    bucket_html = frequency_bars_html(runtime_bucket_counts(ratings_df), label_col="bucket", count_col="count")
    if bucket_html:
        st.markdown(bucket_html, unsafe_allow_html=True)


def _taste_dimension_rows(entries: list[AffinityEntry]) -> list[SignedBarRow]:
    """Format one dimension's Taste-tab rows: top-5 liked, then the 3 most-disliked.

    ``entries`` arrive best-first from :func:`core.taste.dimension_profile`,
    so the disliked tail is simply the list's last rows — the block reads as
    one gradient from strongest like to strongest dislike. Bar widths are
    normalised against the widest |affinity| actually shown; marker (✓/✗
    tier-relative sentiment) and bar sign (μ-relative affinity) are
    independent by design.
    """
    liked = [e for e in entries if e.liked][:5]
    disliked = [e for e in entries if not e.liked][-3:]
    shown = [*liked, *disliked]
    if not shown:
        return []
    widest = max(abs(e.affinity) for e in shown) or 1.0
    return [
        SignedBarRow(
            marker="✓" if e.liked else "✗",
            label=e.value,
            sublabel=f"{'liked' if e.liked else 'disliked'} · {e.n_rated} rated",
            signed_width_pct=e.affinity / widest * 100,
            value_text=f"{e.affinity:+.2f}",
        )
        for e in shown
    ]


def _unresolved_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse one-row-per-screening unresolved data into one row per film.

    ``df`` is expected in :func:`sources.loader.build_unresolved_showtimes`'s shape (one row
    per unresolved film × upcoming showtime). ``next_showtime`` is the soonest upcoming
    screening, ``theaters`` lists every distinct theater currently showing it, and
    ``n_showtimes`` counts the upcoming screenings — turning a screening-level frame into an
    actionable per-film list. Pure/Streamlit-free so it's unit-testable without a page import.
    """
    if df.empty:
        return df
    return (
        df.groupby(["movie", "original_title", "director", "release_year"], dropna=False)
        .agg(
            next_showtime=("showtimes", "min"),
            theaters=("theater_name", lambda s: ", ".join(sorted(set(s.dropna())))),
            n_showtimes=("showtimes", "count"),
        )
        .reset_index()
        .sort_values("next_showtime")
        .reset_index(drop=True)
    )


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2rem;">Movies Database</h1>', unsafe_allow_html=True)

    output_path, allocine_output, _ = get_paths()
    if not output_path:
        st.error("**OUTPUT_PATH** is not set. Add it to the workspace-root `.env` and restart.")
        return

    required = ("data_letterboxd.parquet", "ratings_with_letterboxd.parquet", "watchlist_with_letterboxd.parquet")
    missing = [f for f in required if not (output_path / f).exists()]
    if missing:
        render_empty_state(
            "📥",
            "Letterboxd data missing",
            f"Run `python main.py` in `movies_management` to produce: {', '.join(missing)}.",
        )
        return

    try:
        cache_df = load_letterboxd_cache(str(output_path))
        ratings_df = load_ratings(str(output_path))
        watchlist_df = load_watchlist(str(output_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    avg_rating = ratings_df["user_rating"].mean() if "user_rating" in ratings_df.columns else None
    median_runtime_val = ratings_df["runtime"].median() if "runtime" in ratings_df.columns else None
    render_kpi_strip(
        [
            ("Films rated", len(ratings_df)),
            ("Watchlist size", len(watchlist_df)),
            ("Avg rating", f"{avg_rating:.1f} / 5" if avg_rating else "—"),
            ("Median runtime", format_runtime(median_runtime_val) if median_runtime_val else "—"),
        ]
    )
    cache_file = output_path / "data_letterboxd.parquet"
    render_freshness_banner(cache_file)

    # Also warms the @st.cache_data profile the Recommendations page reads
    # (sources.loader.build_taste_profile formats this same cached object).
    profile = build_affinity(ratings_df)

    tab_overview, tab_taste, tab_discover, tab_tables, tab_unresolved = st.tabs(
        ["📈 Overview", "🧬 Taste", "🔎 Discover", "📋 Tables", "🧩 Unmatched"]
    )

    with tab_overview:
        st.caption(
            f"Stats based on your **{len(ratings_df)} rated films**. "
            f"The Discover tab also includes your watchlist ({len(watchlist_df)} films)."
        )

        hist_col, decade_col = st.columns(2)
        with hist_col:
            st.markdown("##### Your rating scale")
            hist_html = rating_histogram_html(rating_histogram(ratings_df))
            if hist_html:
                st.markdown(hist_html, unsafe_allow_html=True)
                if avg_rating is not None:
                    st.caption(
                        f"μ {avg_rating:.2f} across {len(ratings_df)} films — on your ladder 2.5–3 already means "
                        f'"good", so the low average is the scale working, not disappointment.'
                    )
            else:
                st.caption("No ratings counted yet.")
        with decade_col:
            st.markdown("##### By decade")
            decades_html = decade_profile_html(decade_profile(ratings_df))
            if decades_html:
                st.markdown(decades_html, unsafe_allow_html=True)
                st.caption("Bar length is how many films you rated from that decade; color is the mean rating you gave.")
            else:
                st.caption("No release years to bucket.")

        st.markdown("##### You vs Letterboxd")
        summary = delta_summary(ratings_df)
        if summary["n"]:
            st.caption(
                f"Your rating sits below the community average on {summary['share_below']:.0%} of "
                f"{int(summary['n'])} comparable films (mean gap {summary['mean_delta']:+.2f}) — the tier ladder at "
                "work, since your scale centers near 2.5 where Letterboxd's centers near 3.5. "
                "Below, the films you disagree on hardest, both ways."
            )
            disagreements = _with_detail_url(rating_disagreements(ratings_df)).drop(columns=["slug", "direction"])
            st.dataframe(
                disagreements,
                width="stretch",
                hide_index=True,
                column_config={
                    "detail_url": st.column_config.LinkColumn("Details", display_text="View ↗"),
                    "poster_url": st.column_config.ImageColumn("Poster", width="small"),
                    "title": st.column_config.TextColumn("Title"),
                    "directors": st.column_config.TextColumn("Director(s)"),
                    "release_year": st.column_config.NumberColumn("Year", format="%d"),
                    "user_rating": st.column_config.NumberColumn("You", format="%.1f"),
                    "letterboxd_avg_rating": st.column_config.NumberColumn("Letterboxd", format="%.1f"),
                    "delta": st.column_config.NumberColumn("Δ", format="%+.1f"),
                },
            )
        else:
            st.caption("No community ratings to compare against yet.")

        st.markdown("##### Genre × avg rating (rated films only)")
        _genre_bubble_chart(ratings_df)

        runtime_col, genre_count_col = st.columns(2)
        with runtime_col:
            st.markdown("##### Runtime")
            _runtime_stats(ratings_df)
        with genre_count_col:
            st.markdown("##### Most-watched genres")
            genre_html = frequency_bars_html(genre_counts(ratings_df), label_col="genre", count_col="count")
            if genre_html:
                st.markdown(genre_html, unsafe_allow_html=True)
            else:
                st.caption("No genres to count yet.")

    with tab_taste:
        if profile.is_empty:
            render_empty_state(
                "🧬",
                "No taste profile yet",
                "Rate films on Letterboxd and rerun the movies_management pipeline to build one.",
            )
        else:
            st.caption(
                f"The profile behind every ◎ match badge: signed affinities distilled from your "
                f"{profile.n_ratings} ratings (μ {profile.mu:.2f}), per dimension with its blend weight. "
                "✓/✗ is tier-ladder sentiment; the bar is the value's pull relative to your own average — "
                "a value can be liked and still pull slightly negative."
            )
            dim_cols = st.columns(2)
            shown_dims = 0
            for dim, weight in WEIGHTS.items():
                entries = dimension_profile(profile, dim, min_count=_MIN_COUNT_BY_DIM.get(dim, 1))
                block = affinity_dimension_html(
                    _DIMENSION_LABELS.get(dim, dim),
                    f"weight {weight:g}",
                    _taste_dimension_rows(entries),
                )
                if block:
                    with dim_cols[shown_dims % 2]:
                        st.markdown(block, unsafe_allow_html=True)
                    shown_dims += 1
            if not shown_dims:
                render_empty_state("🧬", "Nothing to show yet", "The rated films carry no usable metadata dimensions.")

    with tab_discover:
        st.markdown("##### Filter your watchlist + ratings")
        all_genres = sorted(explode_tags(cache_df.get("genres", pd.Series(dtype=str))).unique().tolist())
        all_directors = sorted(explode_tags(cache_df.get("directors", pd.Series(dtype=str))).unique().tolist())
        f1, f2, f3, f4 = st.columns([2, 2, 1.3, 1.3])
        with f1:
            sel_genres = st.pills("Genre", options=all_genres, selection_mode="multi", key="db_genre")
        with f2:
            sel_directors = st.multiselect("Director", options=all_directors, placeholder="Search directors…", key="db_director")
        with f3:
            min_rating = st.slider("Min Letterboxd rating", 0.0, 5.0, 0.0, 0.5, key="db_minrating")
        with f4:
            # Only films you've actually rated carry user_rating — watchlist-only
            # films are naturally excluded once this is raised above 0, which is
            # the point: "min your rating" is a rewatch/favorites filter.
            min_user_rating = st.slider("Min your rating", 0.0, 5.0, 0.0, 0.5, key="db_min_user_rating")

        pool = pd.concat([watchlist_df, ratings_df], ignore_index=True).drop_duplicates(subset=["slug"])
        if sel_genres and "genres" in pool.columns:
            pattern = "|".join(g.replace("|", r"\|") for g in sel_genres)
            pool = pool[pool["genres"].fillna("").str.contains(pattern, case=False, regex=True)]
        if sel_directors and "directors" in pool.columns:
            pattern = "|".join(re.escape(d) for d in sel_directors)
            pool = pool[pool["directors"].fillna("").str.contains(pattern, case=False, regex=True)]
        if min_rating > 0 and "letterboxd_avg_rating" in pool.columns:
            pool = pool[pool["letterboxd_avg_rating"].fillna(0) >= min_rating]
        if min_user_rating > 0 and "user_rating" in pool.columns:
            pool = pool[pool["user_rating"].fillna(0) >= min_user_rating]

        if pool.empty:
            render_empty_state("🔍", "No matches", "Loosen the filters to see more films.")
        else:
            # Rank rather than show concat order: the rail shows 18 of possibly
            # hundreds, so the slice must be a best-of, not an arbitrary head.
            if "letterboxd_avg_rating" in pool.columns:
                pool = pool.sort_values("letterboxd_avg_rating", ascending=False, na_position="last")
            sample = pool.head(18).copy()
            if "title" in sample.columns and "letterboxd_title" not in sample.columns:
                sample["letterboxd_title"] = sample["title"]
            render_poster_rail(sample, title=f"{len(pool)} films match · top-rated first")

    with tab_tables:
        subscribed = settings.streaming_service_slugs
        cache_df_s = _with_detail_url(_with_streaming_column(cache_df, str(output_path), subscribed))
        ratings_df_s = _with_detail_url(_with_streaming_column(ratings_df, str(output_path), subscribed))
        watchlist_df_s = _with_detail_url(_with_streaming_column(watchlist_df, str(output_path), subscribed))

        search_col, preset_col = st.columns([2, 3], vertical_alignment="bottom")
        with search_col:
            table_query = st.text_input(
                "Search title or director",
                key="db_table_search",
                placeholder="🔍 Title or director…",
                label_visibility="collapsed",
            )
        with preset_col:
            preset_sel = render_chip_filter(
                "Columns",
                list(TABLE_PRESETS),
                key="db_table_preset",
                selection_mode="single",
                default="Essentials",
                label_visibility="collapsed",
            )
        # Deselecting the preset chip means "no column filter" — show everything.
        preset = preset_sel[0] if preset_sel else "All"

        link_cfg = {
            "detail_url": st.column_config.LinkColumn("Details", display_text="View ↗"),
            "letterboxd_url": st.column_config.LinkColumn("Letterboxd", display_text="Open ↗"),
            "imdb_url": st.column_config.LinkColumn("IMDB", display_text="Open ↗"),
            "tmdb_url": st.column_config.LinkColumn("TMDB", display_text="Open ↗"),
            "poster_url": st.column_config.ImageColumn("Poster", width="small"),
            "streaming_on": st.column_config.TextColumn(
                "Streaming on",
                help="Subscribed services where this film is currently streamable in France (TMDB / JustWatch).",
            ),
        }
        sub_tabs = st.tabs(["Cache", "Ratings", "Watchlist"])
        for sub_tab, frame in zip(sub_tabs, (cache_df_s, ratings_df_s, watchlist_df_s), strict=True):
            with sub_tab:
                shown = filter_table(frame, table_query)
                if shown.empty and table_query.strip():
                    render_empty_state("🔍", "No rows match", "Clear or loosen the search to see this table.")
                else:
                    st.dataframe(
                        shown[preset_columns(shown, preset)],
                        width="stretch",
                        hide_index=True,
                        column_config=link_cfg,
                    )

    with tab_unresolved:
        st.caption(
            "Allocine screenings whose title/director combination couldn't be resolved to a "
            "Letterboxd film during cache enrichment — a spelling mismatch, a director credited "
            "differently between the two sources, or a release too new/obscure to be on "
            "Letterboxd yet. They're still playing; the dashboard just has no page for them. "
            "Check them directly on Letterboxd or Allociné, or wait for a future scrape once the "
            "metadata catches up."
        )
        unresolved_raw = load_unresolved_allocine(str(output_path))
        if unresolved_raw.empty:
            render_empty_state(
                "✅",
                "Every screening matched",
                "The last scrape resolved every Allocine film to a Letterboxd page — nothing to review here.",
            )
        else:
            summary = pd.DataFrame()
            if allocine_output and allocine_output.exists():
                showtimes_df = load_showtimes(str(allocine_output))
                summary = _unresolved_summary(build_unresolved_showtimes(unresolved_raw, showtimes_df))
            if summary.empty:
                n = len(unresolved_raw)
                render_empty_state(
                    "🎬",
                    f"{n} unmatched film{'s' if n != 1 else ''}, no upcoming screenings",
                    "They showed up in the last scrape but none are currently playing — check back after the next refresh.",
                )
            else:
                st.dataframe(
                    summary,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "movie": st.column_config.TextColumn("Title"),
                        "original_title": st.column_config.TextColumn("Original title"),
                        "director": st.column_config.TextColumn("Director"),
                        "release_year": st.column_config.NumberColumn("Year", format="%d"),
                        "theaters": st.column_config.TextColumn("Theater(s)"),
                        "next_showtime": st.column_config.DatetimeColumn("Next showtime", format="ddd D MMM, HH:mm"),
                        "n_showtimes": st.column_config.NumberColumn("Upcoming showtimes"),
                    },
                )


main()
