"""
Recommendations page.

Uses the Hugging Face Inference API to give personalised cinema recommendations based on:
- The user's Letterboxd taste profile (derived from ratings_with_letterboxd.parquet)
- Watchlist movies that are currently showing (inner-join of watchlist + showtimes)
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from huggingface_hub import InferenceClient

load_dotenv(Path(__file__).parents[1] / ".env")

MODEL = "Qwen/Qwen2.5-72B-Instruct"
MAX_TOKENS = 1024


@st.cache_data(ttl=300)
def _load_ratings(movies_output: str) -> pd.DataFrame:
    return pd.read_parquet(Path(movies_output) / "ratings_with_letterboxd.parquet")


@st.cache_data(ttl=120)
def _load_watchlist(movies_output: str) -> pd.DataFrame:
    return pd.read_parquet(Path(movies_output) / "watchlist_with_letterboxd.parquet")


@st.cache_data(ttl=120)
def _load_showtimes(showtimes_path: str) -> pd.DataFrame:
    return pd.read_parquet(showtimes_path)


def _taste_profile(ratings_df: pd.DataFrame) -> str:
    """Return a compact taste summary to embed in the system prompt."""
    if ratings_df.empty or "user_rating" not in ratings_df.columns:
        return "No rating history available."

    avg = ratings_df["user_rating"].mean()

    lines = [f"Average rating given: {avg:.1f}/5"]

    if "genres" in ratings_df.columns:
        exploded = (
            ratings_df[["genres", "user_rating"]]
            .dropna()
            .assign(genre=lambda d: d["genres"].str.split(", "))
            .explode("genre")
        )
        top_genres = (
            exploded.groupby("genre")["user_rating"]
            .mean()
            .sort_values(ascending=False)
            .head(5)
            .index.tolist()
        )
        lines.append(f"Favourite genres: {', '.join(top_genres)}")

    if "directors" in ratings_df.columns:
        exploded_dir = (
            ratings_df[["directors", "user_rating"]]
            .dropna()
            .assign(director=lambda d: d["directors"].str.split(", "))
            .explode("director")
        )
        top_dirs = (
            exploded_dir.groupby("director")["user_rating"]
            .agg(["mean", "count"])
            .query("count >= 2")
            .sort_values("mean", ascending=False)
            .head(5)
            .index.tolist()
        )
        if top_dirs:
            lines.append(f"Favourite directors (≥2 films rated): {', '.join(top_dirs)}")

    return "\n".join(lines)


def _build_watchlist_showtimes(
    showtimes_df: pd.DataFrame,
    watchlist_df: pd.DataFrame,
) -> pd.DataFrame:
    """Same join logic as calendar.py."""
    showtimes_df = showtimes_df.copy()
    watchlist_df = watchlist_df.copy()

    meta_cols = [c for c in ["slug", "title", "runtime", "genres", "letterboxd_avg_rating"] if c in watchlist_df.columns]
    wl_meta = watchlist_df[meta_cols].copy()
    wl_meta = wl_meta.rename(columns={"runtime": "runtime_minutes", "slug": "letterboxd_slug"})
    wl_meta["_key"] = wl_meta["title"].str.lower()

    showtimes_df["_key"] = showtimes_df["movie"].str.lower()
    merged = showtimes_df.merge(wl_meta.drop(columns=["title"]), on="_key", how="inner")

    if "original_title" in showtimes_df.columns:
        unmatched = showtimes_df[~showtimes_df["_key"].isin(merged["_key"])]
        if not unmatched.empty:
            unmatched = unmatched.copy()
            unmatched["_key"] = unmatched["original_title"].str.lower()
            fallback = unmatched.merge(wl_meta.drop(columns=["title"]), on="_key", how="inner")
            merged = pd.concat([merged, fallback], ignore_index=True)

    return merged.drop(columns=["_key"])


def _showtimes_context(wl_shows: pd.DataFrame) -> str:
    wanted = ["movie", "theater_name", "showtimes", "genres", "letterboxd_avg_rating", "runtime_minutes", "director"]
    display_cols = [c for c in wanted if c in wl_shows.columns]
    df = wl_shows[display_cols].sort_values("showtimes").drop_duplicates().reset_index(drop=True)
    return df.to_markdown(index=False)


def _ask_hf(api_key: str, taste: str, showtimes_md: str, history: list[dict]) -> str:
    client = InferenceClient(api_key=api_key)
    system_msg = {
        "role": "system",
        "content": (
            "You are a cinema recommendation assistant helping a film enthusiast choose what to watch.\n\n"
            f"User taste profile (from their Letterboxd ratings history):\n{taste}\n\n"
            f"These are the watchlist movies currently showing at their theaters:\n{showtimes_md}\n\n"
            "Answer questions about these showtimes concisely. Refer to movies by title and include "
            "the theater name and showtime when relevant. Do not invent movies or showtimes not listed above."
        ),
    }
    response = client.chat.completions.create(
        model=MODEL,
        messages=[system_msg] + history,
        max_tokens=MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


def main() -> None:
    st.title("Recommendations")
    st.markdown("Ask about watchlist movies that are currently showing.")

    api_key = os.getenv("HF_API_KEY")
    movies_raw = os.getenv("MOVIES_OUTPUT_PATH")
    allocine_raw = os.getenv("ALLOCINE_OUTPUT_PATH")

    if not api_key:
        st.error("**HF_API_KEY** is not set in `cinema_dashboard/.env`.")
        return
    if not movies_raw:
        st.error("**MOVIES_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    if not allocine_raw:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return

    movies_path = Path(movies_raw)
    showtimes_path = Path(allocine_raw)

    missing = []
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists():
        missing.append("watchlist_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not (movies_path / "ratings_with_letterboxd.parquet").exists():
        missing.append("ratings_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not showtimes_path.exists():
        missing.append("showtimes.parquet — run `python main.py` in `Allocine-Showtimes-Scraping`")
    if missing:
        for m in missing:
            st.warning(f"Missing: {m}")
        return

    try:
        ratings_df = _load_ratings(str(movies_path))
        watchlist_df = _load_watchlist(str(movies_path))
        showtimes_df = _load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    showtimes_df = showtimes_df[showtimes_df["showtimes"] >= pd.Timestamp.now()]
    wl_shows = _build_watchlist_showtimes(showtimes_df, watchlist_df)

    if wl_shows.empty:
        st.info("No upcoming showtimes found for your watchlist movies. Nothing to recommend.")
        return

    n_movies = wl_shows["movie"].nunique()
    n_screenings = len(wl_shows)
    st.caption(f"{n_movies} watchlist movies · {n_screenings} upcoming screenings across your theaters")

    taste = _taste_profile(ratings_df)
    showtimes_md = _showtimes_context(wl_shows)

    if "rec_messages" not in st.session_state:
        st.session_state.rec_messages = []

    for msg in st.session_state.rec_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about what's showing…"):
        st.session_state.rec_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    reply = _ask_hf(api_key, taste, showtimes_md, st.session_state.rec_messages)
                except Exception as exc:
                    reply = f"API error: {exc}"
            st.markdown(reply)

        st.session_state.rec_messages.append({"role": "assistant", "content": reply})

    if st.session_state.rec_messages:
        if st.button("Clear conversation"):
            st.session_state.rec_messages = []
            st.rerun()


main()
