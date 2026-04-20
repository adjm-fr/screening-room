"""
Movies Database page.

Displays statistics from movies_management output parquet files:
- data_letterboxd.parquet    — full metadata cache
- ratings_with_letterboxd.parquet  — user ratings enriched with metadata
- watchlist_with_letterboxd.parquet — user watchlist enriched with metadata
"""

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).parents[1]))
import lib.path_setup  # noqa: F401, E402


def _output_path() -> Path | None:
    raw = os.getenv("MOVIES_OUTPUT_PATH")
    if not raw:
        return None
    return Path(raw)


@st.cache_data(ttl=300)
def _load_data(output_path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p = Path(output_path)
    cache_df = pd.read_parquet(p / "data_letterboxd.parquet")
    ratings_df = pd.read_parquet(p / "ratings_with_letterboxd.parquet")
    watchlist_df = pd.read_parquet(p / "watchlist_with_letterboxd.parquet")
    return cache_df, ratings_df, watchlist_df


def main() -> None:
    st.title("Movies Database")
    st.markdown("Statistics from your Letterboxd ratings and watchlist.")

    output_path = _output_path()
    if not output_path:
        st.error("**MOVIES_OUTPUT_PATH** is not set. Add it to `cinema_dashboard/.env` and restart.")
        return

    missing = [
        f
        for f in ("data_letterboxd.parquet", "ratings_with_letterboxd.parquet", "watchlist_with_letterboxd.parquet")
        if not (output_path / f).exists()
    ]
    if missing:
        st.warning(f"Missing files: {', '.join(missing)}. Run `python main.py` in the `movies_management` project first.")
        return

    try:
        cache_df, ratings_df, watchlist_df = _load_data(str(output_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    # ── Top metrics ──────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Films rated", len(ratings_df))
    col2.metric("Watchlist size", len(watchlist_df))

    avg_rating = ratings_df["user_rating"].mean() if "user_rating" in ratings_df.columns else None
    col3.metric("Avg user rating", f"{avg_rating:.1f} / 5" if avg_rating else "—")

    runtime_col = "runtime" if "runtime" in cache_df.columns else None
    avg_runtime = cache_df[runtime_col].median() if runtime_col else None
    col4.metric("Median runtime", f"{int(avg_runtime)} min" if avg_runtime else "—")

    st.divider()

    # ── Genres distribution ───────────────────────────────────────────────────
    st.subheader("Genres distribution (rated films)")
    if "genres" in ratings_df.columns:
        genres_series = ratings_df["genres"].dropna().str.split(", ").explode().str.strip()
        genres_series = genres_series[genres_series != ""]
        genre_counts = genres_series.value_counts().rename_axis("genre").reset_index(name="count")
        st.bar_chart(genre_counts.set_index("genre")["count"])
    else:
        st.info("No genres data available.")

    # ── Ratings distribution ──────────────────────────────────────────────────
    st.subheader("Your ratings distribution")
    if "user_rating" in ratings_df.columns:
        rating_counts = (
            ratings_df["user_rating"].dropna().value_counts().sort_index().rename_axis("rating").reset_index(name="count")
        )
        st.bar_chart(rating_counts.set_index("rating")["count"])
    else:
        st.info("No user rating data available.")

    # ── Runtime distribution ──────────────────────────────────────────────────
    st.subheader("Runtime distribution (rated films)")
    if runtime_col and "runtime" in ratings_df.columns:
        runtime_data = ratings_df["runtime"].dropna()
        if not runtime_data.empty:
            p25 = int(runtime_data.quantile(0.25))
            p75 = int(runtime_data.quantile(0.75))
            rcol1, rcol2, rcol3 = st.columns(3)
            rcol1.metric("P25 runtime", f"{p25} min")
            rcol2.metric("Median runtime", f"{int(runtime_data.median())} min")
            rcol3.metric("P75 runtime", f"{p75} min")

            bins = list(range(0, int(runtime_data.max()) + 30, 30))
            runtime_hist = (
                pd.cut(runtime_data, bins=bins).value_counts().sort_index().rename_axis("bucket").reset_index(name="count")
            )
            runtime_hist["bucket"] = runtime_hist["bucket"].astype(str)
            st.bar_chart(runtime_hist.set_index("bucket")["count"])
    else:
        st.info("No runtime data available.")

    # ── Cache freshness ───────────────────────────────────────────────────────
    st.subheader("Cache freshness")
    if "integration_date" in cache_df.columns:
        now = pd.Timestamp.now()
        days_to_update = int(os.getenv("LETTERBOXD_DAYS_TO_UPDATE", 365))
        age_days = (now - pd.to_datetime(cache_df["integration_date"])).dt.days
        stale_count = int((age_days > days_to_update).sum())
        total = len(cache_df)

        fc1, fc2 = st.columns(2)
        fc1.metric("Total cached movies", total)
        fc2.metric(f"Stale (> {days_to_update} days)", stale_count)

        oldest = cache_df.loc[age_days.idxmax(), "integration_date"] if total > 0 else None
        if oldest is not None:
            st.caption(f"Oldest entry: {pd.to_datetime(oldest).strftime('%Y-%m-%d')}")
    else:
        st.info("No integration_date column found in cache.")

    # ── Raw data explorer ─────────────────────────────────────────────────────
    with st.expander("Raw data explorer"):
        tab1, tab2, tab3 = st.tabs(["Cache", "Ratings", "Watchlist"])
        with tab1:
            st.dataframe(cache_df)
        with tab2:
            st.dataframe(ratings_df)
        with tab3:
            st.dataframe(watchlist_df)


main()
