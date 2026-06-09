"""
Movies Database page.

Reorganises the Letterboxd cache + ratings + watchlist into three calmer tabs:

- **Overview** — one hero Plotly bubble (genre × avg rating × count) plus
  three micro-card insights (runtime distribution, top directors chip cloud,
  top themes chip cloud). Designed to be scanned, not parsed.
- **Discover** — chip filters (genre, director, min-rating slider) over a
  poster rail of matching films. The taste profile becomes interactive,
  not a static chart wall.
- **Tables** — the three raw dataframes with poster + IMDB/TMDB/Letterboxd
  link columns for power users.
"""

from __future__ import annotations

import html
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from modules.config import settings
from utils.data_loader import (
    attach_streaming,
    build_taste_profile,
    get_paths,
    load_letterboxd_cache,
    load_ratings,
    load_watchlist,
)
from utils.ui import (
    format_runtime,
    rating_to_hsl,
    render_empty_state,
    render_freshness_banner,
    render_kpi_strip,
    render_poster_rail,
)


def _with_streaming_column(
    df: pd.DataFrame,
    movies_output: str,
    subscribed: set[str] | frozenset[str],
) -> pd.DataFrame:
    """Append a ``streaming_on`` column listing subscribed services that carry each film.

    Returns the df unchanged (sans column) when the user has no subscriptions
    configured or the df lacks ``tmdb_id`` — the link table still renders.
    The column is a comma-separated string (empty for unmatched rows) to avoid
    the ``float('nan') → "nan"`` rendering pitfall called out in ``CLAUDE.md``.
    """
    if not subscribed or "tmdb_id" not in df.columns:
        return df
    enriched = attach_streaming(df, movies_output)

    def _label(flat: object) -> str:
        if not isinstance(flat, list):
            return ""
        hits = [p for p in flat if p in subscribed]
        return ", ".join(sorted(hits))

    enriched = enriched.copy()
    enriched["streaming_on"] = enriched["flatrate"].apply(_label)
    return enriched.drop(columns=["flatrate"], errors="ignore")


def _explode_tags(series: pd.Series, separator: str = ", ") -> pd.Series:
    return series.dropna().astype(str).str.split(separator).explode().str.strip().pipe(lambda s: s[s != ""])


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


def _runtime_sparkline(ratings_df: pd.DataFrame) -> None:
    if "runtime" not in ratings_df.columns:
        st.caption("No runtime data.")
        return
    runtimes = ratings_df["runtime"].dropna()
    if runtimes.empty:
        st.caption("No runtime data.")
        return
    p25, p50, p75 = (int(runtimes.quantile(q)) for q in (0.25, 0.5, 0.75))
    st.markdown(
        f"<div class='kpi-label'>Runtime · P25/P50/P75</div>"
        f"<div class='kpi-value'>{format_runtime(p25)} · {format_runtime(p50)} · {format_runtime(p75)}</div>",
        unsafe_allow_html=True,
    )
    bins = list(range(0, int(runtimes.max()) + 30, 30))
    hist = pd.cut(runtimes, bins=bins).value_counts().sort_index()
    spark = px.bar(x=[str(b) for b in hist.index], y=hist.values, labels={"x": "", "y": ""})
    spark.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=80, showlegend=False, xaxis_visible=False, yaxis_visible=False)
    spark.update_traces(marker_color="#E63946", hovertemplate="%{x}: %{y} films<extra></extra>")
    st.plotly_chart(spark, width="stretch")


def _chip_cloud(items: list[tuple[str, float]], *, kind: str = "genre", max_items: int = 8) -> None:
    """Render a static chip cloud where chip color saturation reflects the score."""
    if not items:
        st.caption("Not enough data.")
        return
    chips_html = ""
    for name, score in items[:max_items]:
        bg = rating_to_hsl(score)
        cls = f"chip chip--{kind} chip--rating"
        chips_html += f'<span class="{cls}" style="background:{bg}">{html.escape(name)} · {score:.1f}</span>'
    st.markdown(chips_html, unsafe_allow_html=True)


def _top_directors(ratings_df: pd.DataFrame, *, min_films: int = 2) -> list[tuple[str, float]]:
    if "directors" not in ratings_df.columns or "user_rating" not in ratings_df.columns:
        return []
    exploded = (
        ratings_df[["directors", "user_rating"]]
        .dropna()
        .assign(director=lambda d: d["directors"].str.split(", "))
        .explode("director")
    )
    exploded["director"] = exploded["director"].str.strip()
    summary = (
        exploded.groupby("director")["user_rating"]
        .agg(["mean", "count"])
        .query(f"count >= {min_films}")
        .sort_values("mean", ascending=False)
        .head(8)
    )
    return [(str(idx), float(row["mean"])) for idx, row in summary.iterrows()]


def _top_themes(cache_df: pd.DataFrame) -> list[tuple[str, float]]:
    if "themes" not in cache_df.columns:
        return []
    counts = _explode_tags(cache_df["themes"]).value_counts().head(8)
    if counts.empty:
        return []
    max_count = float(counts.iloc[0])
    return [(str(name), (count / max_count) * 10.0) for name, count in counts.items()]


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2rem;">Movies Database</h1>', unsafe_allow_html=True)

    output_path, _, _ = get_paths()
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

    # Warm the taste-profile cache so the Recommendations page is instant.
    build_taste_profile(ratings_df)

    tab_overview, tab_discover, tab_tables = st.tabs(["📈 Overview", "🔎 Discover", "📋 Tables"])

    with tab_overview:
        st.caption(
            f"Stats based on your **{len(ratings_df)} rated films**. "
            f"The Discover tab also includes your watchlist ({len(watchlist_df)} films)."
        )

        st.markdown("##### Genre × avg rating (rated films only)")
        _genre_bubble_chart(ratings_df)

        c1, c2, c3 = st.columns(3)
        with c1:
            _runtime_sparkline(ratings_df)
        with c2:
            st.markdown("<div class='kpi-label'>Top directors</div>", unsafe_allow_html=True)
            _chip_cloud(_top_directors(ratings_df), kind="genre")
        with c3:
            st.markdown("<div class='kpi-label'>Top themes</div>", unsafe_allow_html=True)
            themes_source = ratings_df if "themes" in ratings_df.columns else cache_df
            _chip_cloud(_top_themes(themes_source), kind="theme")

    with tab_discover:
        st.markdown("##### Filter your watchlist + ratings")
        all_genres = sorted(_explode_tags(cache_df.get("genres", pd.Series(dtype=str))).unique().tolist())
        all_directors = sorted(_explode_tags(cache_df.get("directors", pd.Series(dtype=str))).unique().tolist())
        f1, f2, f3 = st.columns([2, 2, 2])
        with f1:
            sel_genres = st.pills("Genre", options=all_genres, selection_mode="multi", key="db_genre")
        with f2:
            sel_directors = st.multiselect("Director", options=all_directors, placeholder="Search directors…", key="db_director")
        with f3:
            min_rating = st.slider("Min Letterboxd rating", 0.0, 10.0, 0.0, 0.5, key="db_minrating")

        pool = pd.concat([watchlist_df, ratings_df], ignore_index=True).drop_duplicates(subset=["slug"])
        if sel_genres and "genres" in pool.columns:
            pattern = "|".join(g.replace("|", r"\|") for g in sel_genres)
            pool = pool[pool["genres"].fillna("").str.contains(pattern, case=False, regex=True)]
        if sel_directors and "directors" in pool.columns:
            pattern = "|".join(re.escape(d) for d in sel_directors)
            pool = pool[pool["directors"].fillna("").str.contains(pattern, case=False, regex=True)]
        if min_rating > 0 and "letterboxd_avg_rating" in pool.columns:
            pool = pool[pool["letterboxd_avg_rating"].fillna(0) >= min_rating]

        if pool.empty:
            render_empty_state("🔍", "No matches", "Loosen the filters to see more films.")
        else:
            sample = pool.head(18).copy()
            if "title" in sample.columns and "letterboxd_title" not in sample.columns:
                sample["letterboxd_title"] = sample["title"]
            render_poster_rail(sample, title=f"{len(pool)} films match")

    with tab_tables:
        subscribed = settings.streaming_service_slugs
        cache_df_s = _with_streaming_column(cache_df, str(output_path), subscribed)
        ratings_df_s = _with_streaming_column(ratings_df, str(output_path), subscribed)
        watchlist_df_s = _with_streaming_column(watchlist_df, str(output_path), subscribed)

        sub_cache, sub_ratings, sub_watch = st.tabs(["Cache", "Ratings", "Watchlist"])
        link_cfg = {
            "letterboxd_url": st.column_config.LinkColumn("Letterboxd", display_text="Open ↗"),
            "imdb_url": st.column_config.LinkColumn("IMDB", display_text="Open ↗"),
            "tmdb_url": st.column_config.LinkColumn("TMDB", display_text="Open ↗"),
            "poster_url": st.column_config.ImageColumn("Poster", width="small"),
            "streaming_on": st.column_config.TextColumn(
                "Streaming on",
                help="Subscribed services where this film is currently streamable in France (TMDB / JustWatch).",
            ),
        }
        with sub_cache:
            st.dataframe(cache_df_s, width="stretch", hide_index=True, column_config=link_cfg)
        with sub_ratings:
            st.dataframe(ratings_df_s, width="stretch", hide_index=True, column_config=link_cfg)
        with sub_watch:
            st.dataframe(watchlist_df_s, width="stretch", hide_index=True, column_config=link_cfg)


main()
