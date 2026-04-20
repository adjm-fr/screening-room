"""
Showtimes viewer page.

Reads showtimes.parquet produced by Allocine-Showtimes-Scraping and displays
results by theater. Run the scraper CLI to refresh the data.
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")


@st.cache_data(ttl=300)
def _load_showtimes(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)


def main() -> None:
    st.title("Showtimes")
    st.markdown(
        "Upcoming showtimes scraped from Allocine. Head to **Watchlist Calendar** to see which ones match your watchlist."
    )

    raw = os.getenv("ALLOCINE_OUTPUT_PATH")
    if not raw:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    showtimes_path = Path(raw)

    if not showtimes_path.exists():
        st.warning("Showtimes data not found. Run `python main.py` in the `Allocine-Showtimes-Scraping` project first.")
        return

    try:
        df = _load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load showtimes: {exc}")
        return

    if "theater_name" not in df.columns:
        df["theater_name"] = ""

    theater_options = sorted(
        set(df[["theater_id", "theater_name"]].itertuples(index=False, name=None)),
        key=lambda item: ((item[1] or "").lower(), item[0]),
    )

    selected_theater = st.selectbox(
        "Select theater",
        theater_options,
        format_func=lambda item: item[1] if item[1] else item[0],
    )
    selected_theater_id, selected_theater_name = selected_theater

    filtered_df = df[df["theater_id"] == selected_theater_id].copy().sort_values("showtimes")

    col1, col2 = st.columns(2)
    col1.metric("Theater", selected_theater_name or selected_theater_id)
    col2.metric("Showtimes", len(filtered_df))

    st.dataframe(filtered_df.reset_index(drop=True), use_container_width=True)

    if st.checkbox("Show summary metrics", value=False):
        st.subheader("Summary")
        st.write(filtered_df.describe(include="all"))


main()
