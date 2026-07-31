"""
Context assembly and system-prompt construction for the recommendations chat.

:func:`build_chat_context` validates configuration (Gemini key, ``OUTPUT_PATH``,
``ALLOCINE_OUTPUT_PATH``) and on-disk data, loads and joins the
watchlist/showtimes/ratings parquets, and returns a :class:`ChatContext` — the
bundle of taste profile, showtimes/streaming markdown blocks and known
theaters that :func:`build_system_message` renders into the system prompt
anchoring the LLM to a closed set of films/providers (see ``CLAUDE.md``'s
chat-assistant section). Both are called once per Streamlit run, by
``utils.chat``'s callers (``pages/recommendations.py``, ``utils/cmdk.py``).

This module owns:
    ChatContext             (the dataclass)
    build_chat_context()   -> ChatContext | None  (config + data validation)
    build_system_message() -> dict                (the pinned system prompt)
    _streaming_context() / _showtimes_context()    (markdown block builders)

Kept free of any import back into :mod:`utils.chat` or :mod:`utils.chat_state`
— context assembly does not need conversation state or the LLM transport/UI.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pandas as pd
import streamlit as st
from modules.config import settings

from utils.allocine_search import _get_paris_cinemas
from utils.data_loader import (
    attach_streaming,
    build_taste_profile,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from utils.taste import attach_match, build_affinity
from utils.theater_manager import backfill_addresses, load_theaters

log = logging.getLogger(__name__)


def _gemini_key_configured() -> bool:
    """Return True if the Gemini API key is set, else render an error and return False.

    The Streamlit error is rendered here so both chat surfaces share one message.
    """
    if not settings.gemini_api_key:
        st.error("**GEMINI_API_KEY** is not set in the workspace-root `.env`.")
        return False
    return True


@dataclasses.dataclass
class ChatContext:
    """All data the chat needs once configuration is validated."""

    taste: str
    showtimes_md: str
    streaming_md: str
    known_theaters: list[str]
    theaters_csv: Path | None
    wl_shows: pd.DataFrame
    # ``wl_shows`` with the taste ``match`` column joined on (see
    # ``utils.taste.attach_match``) — the frame the ``top_matches`` /
    # ``showtimes_query`` tools query. Falls back to ``wl_shows`` unchanged
    # when there is no usable rating history or scoring fails.
    wl_scored: pd.DataFrame
    n_movies: int
    n_screenings: int


def _streaming_context(wl_shows: pd.DataFrame) -> str:
    """One markdown line per watchlist film with FR streaming availability.

    Empty string when no rows carry streaming data (cache missing or no hits).
    Caller is expected to skip the streaming block in the system prompt when
    this is empty, so the LLM doesn't get distracted by an empty section.

    Line format: ``- {title} — flatrate={a, b}``. This segment is kept
    stable — it's an eval contract (see ``tests/evals/goldens.py``). A
    ``; free={c}`` segment is appended only when the film also has
    free-to-watch providers (Arte.tv, France.tv, …).
    """
    if "flatrate" not in wl_shows.columns:
        return ""
    title_col = "letterboxd_title" if "letterboxd_title" in wl_shows.columns else "french_title"
    lines: list[str] = []
    seen: set[str] = set()
    for _, row in wl_shows.iterrows():
        title = row.get(title_col)
        if not isinstance(title, str) or title in seen:
            continue
        flat = row.get("flatrate") if isinstance(row.get("flatrate"), list) else []
        free = row.get("free") if isinstance(row.get("free"), list) else []
        if not flat and not free:
            continue
        line = f"- {title} — flatrate={', '.join(flat)}"
        if free:
            line += f"; free={', '.join(free)}"
        lines.append(line)
        seen.add(title)
    return "\n".join(lines)


def _showtimes_context(wl_shows: pd.DataFrame) -> str:
    wanted = [
        "french_title",
        "letterboxd_title",
        "theater_name",
        "showtimes",
        "genres",
        "letterboxd_avg_rating",
        "runtime_minutes",
        "directors",
    ]
    display_cols = [c for c in wanted if c in wl_shows.columns]
    df = wl_shows[display_cols].sort_values("showtimes").drop_duplicates().reset_index(drop=True)
    return df.to_markdown(index=False)


def build_chat_context() -> ChatContext | None:
    """Load config + data and return a :class:`ChatContext`, or ``None`` if unusable.

    Renders user-friendly Streamlit error messages for missing config or data
    so callers don't have to repeat the boilerplate. Called from both the
    dedicated page and the ``Cmd+K`` dialog.
    """
    movies_path, showtimes_path, theaters_csv = get_paths()

    if not _gemini_key_configured():
        return None
    if not movies_path:
        st.error("**OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return None
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return None

    if theaters_csv and "theaters_backfilled" not in st.session_state:
        try:
            log.debug("Backfilling theater addresses from Allocine cache")
            updated = backfill_addresses(theaters_csv, _get_paris_cinemas())
            log.info("Address backfill complete: %d row(s) updated", updated)
        except Exception as exc:
            log.warning("Address backfill failed: %s", exc)
        finally:
            st.session_state.theaters_backfilled = True

    missing: list[str] = []
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists():
        missing.append("watchlist_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not (movies_path / "ratings_with_letterboxd.parquet").exists():
        missing.append("ratings_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not showtimes_path.exists():
        missing.append("showtimes.parquet — run `python main.py` in `Allocine-Showtimes-Scraping`")
    if missing:
        for m in missing:
            st.warning(f"Missing: {m}")
        return None

    try:
        ratings_df = load_ratings(str(movies_path))
        watchlist_df = load_watchlist(str(movies_path))
        showtimes_df = load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return None

    showtimes_df = future_showtimes(showtimes_df)
    wl_shows = build_watchlist_showtimes(showtimes_df, watchlist_df)

    if wl_shows.empty:
        st.info("No upcoming showtimes found for your watchlist movies. Nothing to recommend.")
        return None

    watchlist_streaming = attach_streaming(watchlist_df.rename(columns={"title": "letterboxd_title"}), str(movies_path))

    # Score once here rather than per tool call: both chat surfaces and every
    # turn of the conversation reuse the same frame. Scoring is best-effort —
    # a failure costs the tools their ``match`` column, not the whole page.
    try:
        profile = build_affinity(ratings_df)
        wl_scored = attach_match(wl_shows, watchlist_df, profile) if not profile.is_empty else wl_shows
    except Exception as exc:
        log.warning("Taste scoring failed — chat tools fall back to unscored showtimes: %s", exc)
        wl_scored = wl_shows

    showtime_theaters = set(wl_shows["theater_name"].dropna().unique()) if "theater_name" in wl_shows.columns else set()
    csv_theaters = {t["name"] for t in load_theaters(theaters_csv)} if theaters_csv else set()
    known_theaters = sorted(showtime_theaters | csv_theaters)

    return ChatContext(
        taste=build_taste_profile(ratings_df),
        showtimes_md=_showtimes_context(wl_shows),
        streaming_md=_streaming_context(watchlist_streaming),
        known_theaters=known_theaters,
        theaters_csv=theaters_csv,
        wl_shows=wl_shows,
        wl_scored=wl_scored,
        n_movies=int(wl_shows["letterboxd_title"].nunique()),
        n_screenings=int(len(wl_shows)),
    )


def build_system_message(ctx: ChatContext) -> dict:
    """Build the system message used to anchor the LLM to the provided lists.

    Extracted so eval tests can reproduce the exact system prompt without
    depending on Streamlit or the streaming tool-use loop.
    """
    known_theaters_str = "\n".join(f"- {t}" for t in sorted(ctx.known_theaters)) or "None"
    streaming_block = (
        f"\nFR streaming availability for watchlist films (TMDB / JustWatch):\n{ctx.streaming_md}\n"
        if ctx.streaming_md
        else (
            "\nFR streaming availability for watchlist films: NONE — "
            "no watchlist films are currently available on any streaming service.\n"
        )
    )
    return {
        "role": "system",
        "content": (
            "You are a cinema recommendation assistant helping a film enthusiast choose what to watch.\n\n"
            "ABSOLUTE RULE — read first, applies to every response:\n"
            "You may ONLY name films that literally appear in the two data blocks below "
            "('watchlist movies currently showing' or 'FR streaming availability'). This is a closed "
            "set. Treat any film NOT in those blocks as if it does not exist — do not name it, "
            "describe it, compare to it, or acknowledge it, even if the user names it first and even "
            "if you are certain it exists in reality.\n"
            "This rule covers: direct recommendations, 'in the style of X' or 'similar to Y' "
            "suggestions, director filmographies (e.g. if the user asks about Bong Joon-ho, do NOT "
            "name Parasite, Snowpiercer, Memories of Murder, etc. — pick from the provided lists or "
            "say nothing fits), genre comparisons, examples, and apologies.\n"
            "For streaming: you may ONLY pair a film with a provider when that exact (film, provider) "
            "row appears in the 'FR streaming availability' block. Do NOT add providers from outside "
            "knowledge, even if you are certain the film streams there in reality.\n"
            "If nothing in the provided lists fits, say so plainly without naming any outside film "
            "or provider.\n\n"
            "STYLE-ANCHOR REQUESTS — when the user names a film or director as a COMPARISON or "
            "STYLE REFERENCE rather than asking for that specific title (e.g. 'in the style of X', "
            "'a X-style movie', 'like X', 'similar to Y', 'reminds me of X', 'something Bong "
            "Joon-ho-ish'):\n"
            "1. Do NOT refuse and do NOT treat this as an out-of-list request. The named "
            "film/director is a STYLE CUE telling you what to match — not a request for that "
            "specific work.\n"
            "2. Recommend one or more films FROM the provided lists whose mood, themes, tone, or "
            "craft best fit that style, and say in one line why each fits.\n"
            "3. NEVER name the referenced film/director's own works or any other outside film. If "
            "genuinely nothing in the provided lists matches the style, say so plainly and offer "
            "the closest available alternative — still without naming any outside film.\n\n"
            "REFUSAL FLOW — when the user asks FOR a specific film, a specific director's own "
            "filmography, or a specific provider that is NOT in the provided lists (e.g. 'do you "
            "have Oppenheimer?', 'anything by Nolan tonight?', 'is Parasite on Disney+?'), and is "
            "NOT making a style-anchor request as defined above:\n"
            "1. Respond in 1-2 sentences. Briefly state that the film/director/provider isn't in "
            "their watchlist or streaming availability.\n"
            "2. End by asking whether they'd like a recommendation from what IS available "
            "(e.g. 'Would you like me to suggest something from your watchlist or streaming "
            "list instead?').\n"
            "3. Do NOT list watchlist films, showtimes, or streaming options in this refusal. "
            "Wait for the user to confirm before producing recommendations.\n\n"
            "THEATER LOOKUP — the ONE exception to the refusal flow above, handled with a TOOL "
            "instead of a refusal. When the user names or asks about ANY theater that is not in the "
            "'Known theaters' list below — including pure membership questions such as 'is Brady in "
            "the theater list?', 'do you know the Brady cinema?', or 'what about the Brady?' — you "
            "MUST call the search_theater tool with that theater name BEFORE writing any reply. Do "
            "NOT answer from the known list, do NOT say the theater is unknown or has no data, and "
            "do NOT ask the user whether they'd like you to search — just call search_theater. The "
            "refusal flow does NOT apply to theaters.\n\n"
            "TASTE & SHOWTIME TOOLS — two read-only tools query the SAME closed set as the data blocks "
            "below. Call top_matches when the user asks what they would most enjoy ('what are my top "
            "matches tonight?', 'what should I prioritise?'), optionally narrowed to a genre; it ranks "
            "their OWN watchlist films by their taste profile. Call showtimes_query for a targeted "
            "showtime lookup ('when is X playing?', 'what's on at the Champo on Saturday?'), passing the "
            "day as an ISO date. Their results are the only additional source of rankings and showtimes "
            "you may cite — every row they return already belongs to the closed set, and the ABSOLUTE "
            "RULE still holds: never name a film, provider or theater that appears neither in a tool "
            "result nor in the data blocks below.\n\n"
            f"User taste profile (from their Letterboxd ratings history):\n{ctx.taste}\n\n"
            f"These are the watchlist movies currently showing at their theaters:\n{ctx.showtimes_md}\n"
            f"{streaming_block}\n"
            f"Known theaters (the only ones with showtimes data):\n{known_theaters_str}\n\n"
            "Other rules:\n"
            "- Answer questions about the showtimes above concisely.\n"
            "- Refer to movies by title and include theater name and showtime when relevant.\n"
            "- The taste profile describes the user's preferences (genres, directors, themes) for "
            "STYLE matching only. Use it to pick which provided films to suggest — NEVER as a source "
            "of titles, director filmographies, or 'similar films' from outside the provided lists. "
            "The user's ratings follow a strict tier ladder — 2.5–3/5 already means a good film, "
            "3.5+/5 a must-watch — so never interpret their low rating average as dissatisfaction.\n"
            "- For any theater not in the known theaters list, follow the THEATER LOOKUP rule above "
            "(call search_theater); never say the theater has no data."
        ),
    }
