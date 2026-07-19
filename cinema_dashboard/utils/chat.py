"""
Reusable chat renderer for the cinema recommendations assistant.

The chat is mounted in two places:

- ``pages/recommendations.py`` — the dedicated full-page surface (with prompt
  chips, pinned recommendations column, conversation export)
- ``utils/cmdk.py`` — the global ``Cmd+K`` dialog (compact variant, no pinned
  column, shares the same conversation state via Streamlit ``session_state``)

Both surfaces share a single ``session_state['chat']`` (a :class:`ChatState`
dataclass) so the conversation persists across them. The transcript and pinned
recommendations are additionally persisted to ``data/chat_state.json``
(:data:`CHAT_STATE_PATH`, gitignored beside the streaming/geo caches) so they
survive app restarts — loaded on first session access, saved after each
assistant turn and pin change, deleted by "Clear conversation".

This module owns:
    build_chat_context()  -> ChatContext | None  (config + data validation)
    render_chat(ctx, ...) -> None                (the UI)
    PROMPT_SUGGESTIONS    : list[str]            (chip-row examples)
    save_chat_state() / load_chat_state() / delete_chat_state()  (disk persistence)

The Gemini API call lives in :func:`_ask_gemini` which streams the assistant
reply and handles a single round of ``search_theater`` tool use.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
from modules.config import settings

from utils.allocine_search import _get_paris_cinemas, search_theaters
from utils.data_loader import (
    _normalize_title,
    attach_streaming,
    build_taste_profile,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from utils.theater_manager import append_theater, backfill_addresses, load_theater_ids, load_theaters
from utils.ui import render_movie_card

log = logging.getLogger(__name__)


@dataclasses.dataclass
class ChatState:
    """All conversational state for the chat, kept in one place.

    Stored under ``st.session_state['chat']`` and shared by both chat surfaces
    (the dedicated page and the ``Cmd+K`` dialog). Widget-bound keys
    (``prompt_chips``, ``pin_picker``, ``_cmdk_btn``) and one-shot session flags
    (``theaters_backfilled``, ``_cmdk_open``) live outside this dataclass on
    purpose. Reset the conversation by replacing the object:
    ``st.session_state['chat'] = ChatState()``.

    Only ``messages`` and ``pinned_recs`` are persisted to disk
    (:func:`save_chat_state`); the remaining fields are per-run ephemera and
    stay session-only.
    """

    messages: list[dict] = dataclasses.field(default_factory=list)
    pending_theaters: list[dict] | None = None
    pinned_recs: list[dict] = dataclasses.field(default_factory=list)
    pinnable: list[str] = dataclasses.field(default_factory=list)
    last_chip: str | None = None
    pending_prompt: str | None = None


# On-disk snapshot of the conversation (transcript + pinned recommendations),
# stored in the gitignored ``data/`` dir beside the streaming/geo caches.
# Module-level so tests can patch it (same pattern as
# ``utils.streaming.PROVIDER_DISPLAY_NAMES_PATH``); the helpers below resolve
# it at call time, not at function-definition time.
CHAT_STATE_PATH = Path("data") / "chat_state.json"


def save_chat_state(state: ChatState, path: Path | None = None) -> None:
    """Persist the transcript and pinned recommendations to ``path``.

    Pinned rows carry ``pd.Timestamp`` values, serialized via ``default=str``;
    the pinned renderer re-parses them with ``pd.to_datetime``, so string
    dates round-trip fine.
    """
    path = path or CHAT_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"messages": state.messages, "pinned_recs": state.pinned_recs}, f, ensure_ascii=False, default=str)


def load_chat_state(path: Path | None = None) -> ChatState:
    """Return the persisted :class:`ChatState`, or a fresh one when unavailable.

    An absent file (normal first run) and a corrupt/unreadable one (logged as
    a warning) both yield a fresh state — loading never crashes the page.
    """
    path = path or CHAT_STATE_PATH
    if not path.exists():
        return ChatState()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        messages = data.get("messages") or []
        pinned_recs = data.get("pinned_recs") or []
        if not isinstance(messages, list) or not isinstance(pinned_recs, list):
            raise ValueError("'messages' and 'pinned_recs' must be lists")
        return ChatState(messages=messages, pinned_recs=pinned_recs)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError subclasses ValueError
        log.warning("Discarding unreadable chat state at %s: %s", path, exc)
        return ChatState()


def delete_chat_state(path: Path | None = None) -> None:
    """Delete the persisted chat state; a missing file is a no-op."""
    (path or CHAT_STATE_PATH).unlink(missing_ok=True)


def chat_state() -> ChatState:
    """Return the shared :class:`ChatState`, creating it on first access.

    First access loads the persisted transcript + pins from
    :data:`CHAT_STATE_PATH`, so the conversation survives app restarts.
    """
    if "chat" not in st.session_state:
        st.session_state["chat"] = load_chat_state()
    return cast(ChatState, st.session_state["chat"])


PROMPT_SUGGESTIONS = [
    "What's playing tonight?",
    "Pick a short film for after work",
    "Surprise me with a Bong Joon-ho-style movie",
    "What can I watch this weekend?",
]

SEARCH_THEATER_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_theater",
            description=(
                "Search Allocine for Paris cinemas matching a name. "
                "Call this whenever the user names or asks about any theater that is NOT in the known "
                "theaters list — including plain membership questions like 'is X in the list?', 'do you "
                "know the X cinema?', or 'what about X?'. Always call the tool instead of answering from "
                "the known list; never tell the user the theater is unknown or ask whether to search — "
                "just search."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The theater's distinctive name only, e.g. 'Brady' — strip generic words "
                            "like 'cinema', 'theater', 'the', so it substring-matches the Allocine name."
                        ),
                    )
                },
                required=["query"],
            ),
        )
    ]
)


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


def _history_to_contents(history: list[dict]) -> list[types.Content]:
    """Map OpenAI-style chat history (``role`` in ``{user, assistant}``) to Gemini ``Content``s.

    Gemini uses ``"model"`` where OpenAI uses ``"assistant"``. Only text turns
    are stored in ``rec_messages`` — tool exchanges are added inline in
    :func:`_ask_gemini` and not persisted to history.
    """
    contents: list[types.Content] = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
    return contents


def _ask_gemini(ctx: ChatContext, history: list[dict]) -> tuple[Iterator[str], list]:
    """Stream a Gemini chat response, handling one round of ``search_theater`` tool use.

    Returns ``(text_stream, pending_ref)`` where ``pending_ref`` is a
    single-element list populated *after* the generator is exhausted with the
    list of theater suggestions awaiting user confirmation (or ``None``).
    """
    log.debug("Calling Gemini API — model: %s, history length: %d messages", settings.gemini_model, len(history))
    client = genai.Client(api_key=settings.gemini_api_key)
    system_instruction = build_system_message(ctx)["content"]
    contents = _history_to_contents(history)
    cfg = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[SEARCH_THEATER_TOOL],
        max_output_tokens=settings.gemini_max_tokens,
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
    )
    pending_ref: list[list[dict] | None] = [None]

    def _generate() -> Iterator[str]:
        fn_call: types.FunctionCall | None = None
        assistant_parts: list[types.Part] = []

        stream = client.models.generate_content_stream(model=settings.gemini_model, contents=cast(list, contents), config=cfg)
        for chunk in stream:
            if not chunk.candidates or chunk.candidates[0].content is None:
                continue
            for part in chunk.candidates[0].content.parts or []:
                if part.text:
                    assistant_parts.append(part)
                    yield part.text
                elif part.function_call:
                    fn_call = part.function_call
                    assistant_parts.append(part)

        if fn_call is None:
            return

        query = (fn_call.args or {}).get("query", "")
        log.info("Tool call: search_theater(query=%r)", query)
        results = search_theaters(query)
        log.info("search_theater returned %d result(s)", len(results))

        # Surface the tool call to the UI as a transparent expander.
        with st.expander(f"🛠 Searched theaters: {query}", expanded=False):
            if results:
                st.dataframe(pd.DataFrame(results), width="stretch", hide_index=True)
            else:
                st.caption("No matches.")

        follow_contents = contents + [
            types.Content(role="model", parts=assistant_parts),
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="search_theater", response={"results": results})],
            ),
        ]
        follow_up = client.models.generate_content_stream(
            model=settings.gemini_model, contents=cast(list, follow_contents), config=cfg
        )
        for chunk in follow_up:
            if not chunk.candidates or chunk.candidates[0].content is None:
                continue
            for part in chunk.candidates[0].content.parts or []:
                if part.text:
                    yield part.text

        pending_ref[0] = results if results else None

    return _generate(), pending_ref


def _find_pinnable_titles(reply_text: str, wl_shows: pd.DataFrame) -> list[str]:
    """Return watchlist titles that appear (case/accent-insensitive) in ``reply_text``."""
    if wl_shows.empty or "letterboxd_title" not in wl_shows.columns:
        return []
    norm_reply = _normalize_title(reply_text)
    titles = wl_shows["letterboxd_title"].dropna().unique().tolist()
    matches = [t for t in titles if _normalize_title(t) and _normalize_title(t) in norm_reply]
    return sorted(set(matches))


def _render_pending_theaters(ctx: ChatContext) -> None:
    state = chat_state()
    if not state.pending_theaters or not ctx.theaters_csv:
        return
    st.divider()
    st.markdown("**Found these Paris theaters — add one to your list?**")
    for theater in state.pending_theaters:
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{theater['name']}** — {theater.get('address', '')}")
        if col2.button("Add", key=f"add_{theater['id']}"):
            added = append_theater(ctx.theaters_csv, theater["id"], theater["name"], theater.get("address", ""))
            if added:
                st.success(f"Added **{theater['name']}**. Re-run the Allocine scraper to fetch its showtimes.")
            else:
                st.info(f"**{theater['name']}** is already in your theater list.")
            state.pending_theaters = None
            st.rerun()
    if st.button("Dismiss", key="dismiss_pending"):
        state.pending_theaters = None
        st.rerun()
    st.divider()


def render_chat(ctx: ChatContext, *, show_prompt_chips: bool = True, show_pinned_column: bool = True) -> None:
    """Render the chat UI: prompt chips, history, streaming response, pending theaters.

    When ``show_pinned_column`` is True (page surface), the chat occupies a
    2/3 column with pinned recommendations on the right. When False (dialog
    surface), the chat fills the available width.
    """
    state = chat_state()

    if show_pinned_column:
        chat_col, pinned_col = st.columns([2, 1])
    else:
        chat_col = st.container()
        pinned_col = None

    with chat_col:
        st.caption(f"Model: `{settings.gemini_model}` · {ctx.n_movies} watchlist movies · {ctx.n_screenings} upcoming screenings")

        if show_prompt_chips and not state.messages:
            chosen = st.pills(
                "Try a prompt",
                options=PROMPT_SUGGESTIONS,
                selection_mode="single",
                key="prompt_chips",
            )
            if chosen and state.last_chip != chosen:
                state.last_chip = chosen
                state.pending_prompt = chosen
                st.rerun()

        _render_pending_theaters(ctx)

        for msg in state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        queued = state.pending_prompt
        state.pending_prompt = None
        prompt = queued or st.chat_input("Ask about what's showing…")
        if prompt:
            state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.status("Thinking…", expanded=False) as status:
                    try:
                        stream, pending_ref = _ask_gemini(ctx, state.messages)
                    except Exception as exc:
                        log.exception("Gemini API call failed")
                        reply: str = f"API error: {exc}"
                        pending: list[dict] | None = None
                        st.markdown(reply)
                        status.update(label="Failed", state="error")
                    else:
                        reply = cast(str, st.write_stream(stream))
                        pending = pending_ref[0]
                        status.update(label="Done", state="complete")

            state.messages.append({"role": "assistant", "content": reply})
            save_chat_state(state)

            pinnable = _find_pinnable_titles(reply, ctx.wl_shows)
            if pinnable:
                state.pinnable = pinnable

            if pending and ctx.theaters_csv:
                existing_ids = load_theater_ids(ctx.theaters_csv)
                new_pending = [t for t in pending if t["id"] not in existing_ids]
                state.pending_theaters = new_pending if new_pending else None
            st.rerun()

        if state.messages:
            c1, c2 = st.columns(2)
            with c1:
                conv_md = "\n\n".join(f"### {m['role'].title()}\n\n{m['content']}" for m in state.messages)
                st.download_button(
                    "💾 Save conversation",
                    data=conv_md.encode("utf-8"),
                    file_name="recommendations_conversation.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
            with c2:
                if st.button("🗑 Clear conversation", use_container_width=True):
                    st.session_state["chat"] = ChatState()
                    delete_chat_state()
                    st.rerun()

    if pinned_col is not None:
        with pinned_col:
            st.markdown("##### 📌 Pinned")
            if state.pinnable:
                to_pin = st.multiselect(
                    "Pin from this reply",
                    options=state.pinnable,
                    key="pin_picker",
                    label_visibility="collapsed",
                )
                if to_pin:
                    existing = {p["letterboxd_title"] for p in state.pinned_recs}
                    n_before = len(state.pinned_recs)
                    for title in to_pin:
                        if title in existing:
                            continue
                        match = ctx.wl_shows[ctx.wl_shows["letterboxd_title"] == title].head(1)
                        if not match.empty:
                            state.pinned_recs.append(match.iloc[0].to_dict())
                    if len(state.pinned_recs) > n_before:
                        save_chat_state(state)

            if not state.pinned_recs:
                st.caption("Pinned recommendations will appear here.")
            else:
                for pinned in state.pinned_recs:
                    render_movie_card(pd.Series(pinned), size="sm")
                    showtime = pinned.get("showtimes")
                    theater = pinned.get("theater_name")
                    if showtime is not None and not pd.isna(showtime):
                        when = pd.to_datetime(showtime).strftime("%a %d %b · %H:%M")
                        st.caption(f"🎟 {when}{f' — {theater}' if isinstance(theater, str) and theater else ''}")
                if st.button("Clear pins", key="clear_pins", use_container_width=True):
                    state.pinned_recs = []
                    save_chat_state(state)
                    st.rerun()
