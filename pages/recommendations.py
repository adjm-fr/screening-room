"""
Recommendations page.

Uses the Hugging Face Inference API to give personalised cinema recommendations based on:
- The user's Letterboxd taste profile (derived from ratings_with_letterboxd.parquet)
- Watchlist movies that are currently showing (inner-join of watchlist + showtimes)

Also supports adding new Paris theaters via tool use: when the user mentions an unknown
theater, the model calls search_theater(), results are returned as a tool message, and
the user can confirm to append the theater to the theaters CSV.
"""

import json
import logging
import os
from collections.abc import Iterator

import pandas as pd
import streamlit as st
from huggingface_hub import InferenceClient

from utils.allocine_search import _get_paris_cinemas, search_theaters
from utils.data_loader import build_watchlist_showtimes, future_showtimes, get_paths, load_ratings, load_showtimes, load_watchlist
from utils.theater_manager import append_theater, backfill_addresses, load_theater_ids, load_theaters

log = logging.getLogger(__name__)

MODEL = "Qwen/Qwen2.5-72B-Instruct"
MAX_TOKENS = 1024

SEARCH_THEATER_TOOL = {
    "type": "function",
    "function": {
        "name": "search_theater",
        "description": (
            "Search Allocine for Paris cinemas matching a name. "
            "Call this when the user asks about a theater that is not in the current showtimes data."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Theater name to search for"}},
            "required": ["query"],
        },
    },
}


def _taste_profile(ratings_df: pd.DataFrame) -> str:
    """Return a compact taste summary to embed in the system prompt."""
    if ratings_df.empty or "user_rating" not in ratings_df.columns:
        log.warning("Ratings DataFrame empty or missing user_rating — taste profile unavailable")
        return "No rating history available."

    avg = ratings_df["user_rating"].mean()
    lines = [f"Average rating given: {avg:.1f}/5"]

    if "genres" in ratings_df.columns:
        exploded = (
            ratings_df[["genres", "user_rating"]].dropna().assign(genre=lambda d: d["genres"].str.split(", ")).explode("genre")
        )
        top_genres = exploded.groupby("genre")["user_rating"].mean().sort_values(ascending=False).head(5).index.tolist()
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

    profile = "\n".join(lines)
    log.debug("Taste profile:\n%s", profile)
    return profile


def _showtimes_context(wl_shows: pd.DataFrame) -> str:
    wanted = ["movie", "theater_name", "showtimes", "genres", "letterboxd_avg_rating", "runtime_minutes", "director"]
    display_cols = [c for c in wanted if c in wl_shows.columns]
    df = wl_shows[display_cols].sort_values("showtimes").drop_duplicates().reset_index(drop=True)
    return df.to_markdown(index=False)


def _ask_hf(
    api_key: str,
    taste: str,
    showtimes_md: str,
    known_theaters: list[str],
    history: list[dict],
) -> tuple[Iterator[str], list]:
    """
    Stream a response from the HF API, handling one round of tool use if the model
    calls search_theater.

    Returns (text_stream, pending_ref) where:
    - text_stream is a generator of string chunks suitable for st.write_stream()
    - pending_ref is a single-element list; after the generator is exhausted,
      pending_ref[0] holds either a list of {id, name, address} dicts awaiting user
      confirmation, or None if no tool was called.

    The two-element return keeps the call site simple: consume the stream first
    (st.write_stream blocks until done), then read pending_ref[0].

    known_theaters is injected into the system prompt so the model can detect unknown
    theaters and trigger the search_theater tool reliably.
    """
    log.debug("Calling HF API — model: %s, history length: %d messages", MODEL, len(history))
    log.debug("Known theaters passed to model: %s", known_theaters)

    client = InferenceClient(api_key=api_key)
    known_theaters_str = "\n".join(f"- {t}" for t in sorted(known_theaters)) or "None"
    system_msg = {
        "role": "system",
        "content": (
            "You are a cinema recommendation assistant helping a film enthusiast choose what to watch.\n\n"
            f"User taste profile (from their Letterboxd ratings history):\n{taste}\n\n"
            f"These are the watchlist movies currently showing at their theaters:\n{showtimes_md}\n\n"
            f"Known theaters (the only ones with showtimes data):\n{known_theaters_str}\n\n"
            "Rules:\n"
            "- Answer questions about the showtimes above concisely.\n"
            "- Refer to movies by title and include theater name and showtime when relevant.\n"
            "- Do not invent movies or showtimes not listed above.\n"
            "- If the user mentions a theater that is NOT in the known theaters list above, "
            "you MUST call search_theater before responding — do not say the theater has no data."
        ),
    }
    messages = [system_msg] + history

    # pending_ref[0] is populated by the generator after it finishes streaming.
    # Using a list so the generator closure can write back to the caller.
    pending_ref: list[list[dict] | None] = [None]

    def _generate() -> Iterator[str]:
        # Stream the first call; accumulate tool-call deltas without yielding them
        # (tool calls produce no visible content — the text comes in the follow-up).
        tool_call_id = ""
        tool_call_name = ""
        tool_call_args = ""
        is_tool_call = False

        stream = client.chat.completions.create(  # type: ignore[call-overload]
            model=MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            tools=[SEARCH_THEATER_TOOL],  # type: ignore[arg-type]
            tool_choice="auto",
            stream=True,
        )

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
            if delta.tool_calls:
                is_tool_call = True
                for tc in delta.tool_calls:
                    if tc.id:
                        tool_call_id = tc.id
                    if tc.function:
                        tool_call_name += tc.function.name or ""
                        tool_call_args += tc.function.arguments or ""

        if not is_tool_call:
            log.debug("Stream complete — no tool call")
            return

        # Tool was called: execute it, then stream the follow-up response.
        args = json.loads(tool_call_args)
        query = args.get("query", "")
        log.info("Tool call: search_theater(query=%r)", query)
        results = search_theaters(query)
        log.info("search_theater returned %d result(s): %s", len(results), [r.get("name") for r in results])

        assistant_msg = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": tool_call_name, "arguments": tool_call_args},
                }
            ],
        }
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(results),
        }

        follow_up = client.chat.completions.create(
            model=MODEL,
            messages=messages + [assistant_msg, tool_result_msg],
            max_tokens=MAX_TOKENS,
            stream=True,
        )
        total_chars = 0
        for chunk in follow_up:
            delta_content = chunk.choices[0].delta.content
            if delta_content:
                total_chars += len(delta_content)
                yield delta_content

        pending_ref[0] = results if results else None
        log.debug(
            "Follow-up stream complete — %d chars, pending theaters: %s",
            total_chars,
            [r.get("name") for r in (pending_ref[0] or [])],
        )

    return _generate(), pending_ref


def main() -> None:
    st.title("Recommendations")
    st.markdown("Ask about watchlist movies that are currently showing.")

    api_key = os.getenv("HF_API_KEY")
    movies_path, showtimes_path, theaters_csv = get_paths()

    if not api_key:
        st.error("**HF_API_KEY** is not set in `cinema_dashboard/.env`.")
        return
    if not movies_path:
        st.error("**MOVIES_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in `cinema_dashboard/.env`.")
        return

    # Backfill missing addresses in the theaters CSV from the Allocine cache.
    # Runs once per session (cheap: the cinema list is already cached in memory).
    if theaters_csv and "theaters_backfilled" not in st.session_state:
        try:
            log.debug("Backfilling theater addresses from Allocine cache")
            updated = backfill_addresses(theaters_csv, _get_paris_cinemas())
            log.info("Address backfill complete: %d row(s) updated", updated)
            st.session_state.theaters_backfilled = True
        except Exception as exc:
            log.warning("Address backfill failed: %s", exc)
            st.session_state.theaters_backfilled = True  # don't retry on error

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
        ratings_df = load_ratings(str(movies_path))
        watchlist_df = load_watchlist(str(movies_path))
        showtimes_df = load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    showtimes_df = future_showtimes(showtimes_df)
    wl_shows = build_watchlist_showtimes(showtimes_df, watchlist_df)

    if wl_shows.empty:
        st.info("No upcoming showtimes found for your watchlist movies. Nothing to recommend.")
        return

    n_movies = wl_shows["movie"].nunique()
    n_screenings = len(wl_shows)
    st.caption(f"{n_movies} watchlist movies · {n_screenings} upcoming screenings across your theaters")

    taste = _taste_profile(ratings_df)
    showtimes_md = _showtimes_context(wl_shows)
    # Union of theaters with current showtimes AND theaters already in the CSV.
    # This prevents re-prompting to add a theater that was just added but not yet scraped.
    showtime_theaters = set(wl_shows["theater_name"].dropna().unique()) if "theater_name" in wl_shows.columns else set()
    csv_theaters = {t["name"] for t in load_theaters(theaters_csv)} if theaters_csv else set()
    known_theaters = sorted(showtime_theaters | csv_theaters)
    log.debug("Known theaters (%d): %s", len(known_theaters), known_theaters)

    if "rec_messages" not in st.session_state:
        st.session_state.rec_messages = []
    if "pending_theaters" not in st.session_state:
        st.session_state.pending_theaters = None

    # ── Pending theater confirmation ──────────────────────────────────────────
    if st.session_state.pending_theaters and theaters_csv:
        st.divider()
        st.markdown("**Found these Paris theaters — add one to your list?**")
        for theater in st.session_state.pending_theaters:
            col1, col2 = st.columns([4, 1])
            col1.markdown(f"**{theater['name']}** — {theater.get('address', '')}")
            if col2.button("Add", key=f"add_{theater['id']}"):
                log.info("User clicked Add for theater %s (%s)", theater["name"], theater["id"])
                added = append_theater(theaters_csv, theater["id"], theater["name"], theater.get("address", ""))
                if added:
                    log.info("Theater %s (%s) appended to %s", theater["name"], theater["id"], theaters_csv)
                    st.success(
                        f"Added **{theater['name']}** to your theater list. Re-run the Allocine scraper to fetch its showtimes."
                    )
                else:
                    log.info("Theater %s (%s) already in CSV — skipped", theater["name"], theater["id"])
                    st.info(f"**{theater['name']}** is already in your theater list.")
                st.session_state.pending_theaters = None
                st.rerun()
        if st.button("Dismiss"):
            st.session_state.pending_theaters = None
            st.rerun()
        st.divider()

    # ── Chat history ──────────────────────────────────────────────────────────
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
                    reply, pending = _ask_hf(api_key, taste, showtimes_md, known_theaters, st.session_state.rec_messages)
                except Exception as exc:
                    log.exception("HF API call failed")
                    reply, pending = f"API error: {exc}", None
            st.markdown(reply)

        st.session_state.rec_messages.append({"role": "assistant", "content": reply})
        if pending and theaters_csv:
            existing_ids = load_theater_ids(theaters_csv)
            new_pending = [t for t in pending if t["id"] not in existing_ids]
            log.debug(
                "Pending theaters after dedup: %d (filtered %d already-tracked)",
                len(new_pending),
                len(pending) - len(new_pending),
            )
            st.session_state.pending_theaters = new_pending if new_pending else None
        st.rerun()

    if st.session_state.rec_messages:
        if st.button("Clear conversation"):
            st.session_state.rec_messages = []
            st.session_state.pending_theaters = None
            st.rerun()


main()
