"""
Reusable chat renderer for the cinema recommendations assistant.

The chat is mounted in two places:

- ``pages/recommendations.py`` — the dedicated full-page surface (with prompt
  chips, pinned recommendations column, conversation export)
- ``ui/cmdk.py`` — the global ``Cmd+K`` dialog (compact variant, no pinned
  column, shares the same conversation state via Streamlit ``session_state``)

Both surfaces share a single ``session_state['chat']`` (a :class:`ChatState`
dataclass, defined in :mod:`chat.state`) so the conversation persists
across them; that module also owns the ``data/chat_state.json`` disk
persistence. Context assembly — :class:`ChatContext`, ``build_chat_context``,
and the pinned ``build_system_message`` prompt — lives in
:mod:`chat.prompt`. The backward-compatible ``from chat import ...`` surface
for both modules' names is provided by :mod:`chat` (the package ``__init__``),
not by this module.

This module itself owns the LLM transport and the UI:
    render_chat(ctx, ...) -> None      (the UI: history, streaming reply, pins)
    PROMPT_SUGGESTIONS    : list[str]  (chip-row examples)

The Gemini API call lives in :func:`_ask_gemini`, which streams the assistant
reply and handles up to :data:`MAX_TOOL_ROUNDS` rounds of tool use, dispatched
by :func:`_run_tool`: ``search_theater`` (Allocine lookup, defined here) plus
``top_matches`` / ``showtimes_query`` (pure queries over the injected data,
defined in :mod:`chat.tools`). ``_run_tool`` and ``_render_tool_rows``
render Streamlit expanders (transport-with-UI), which is why they stay here
beside ``_ask_gemini`` rather than in the Streamlit-free ``chat.tools``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import cast

import pandas as pd
import streamlit as st
from config import settings
from google import genai
from google.genai import types
from integrations.allocine import search_theaters
from integrations.theaters import append_theater, load_theater_ids
from sources.loader import _normalize_title
from ui import render_movie_card

from chat.prompt import ChatContext, build_system_message
from chat.state import ChatState, chat_state, delete_chat_state, save_chat_state
from chat.tools import SHOWTIMES_TOOL, TASTE_TOOL, showtimes_query, top_matches

log = logging.getLogger(__name__)


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


# How many tool calls one user turn may trigger. Bounded so a model that keeps
# asking for tools can't loop forever (or drain the token budget); the reply is
# still streamed, the surplus tool call is simply ignored.
MAX_TOOL_ROUNDS = 2


def _run_tool(ctx: ChatContext, name: str, args: dict) -> tuple[dict, list[dict] | None]:
    """Execute one tool call and surface it in the UI as a transparent expander.

    Returns ``(response_payload, theater_results)``: the payload goes back to
    Gemini as the function response, and ``theater_results`` is non-``None``
    only for ``search_theater`` — the one tool whose output also drives the
    "add this theater?" confirmation flow (:func:`_render_pending_theaters`).

    An unknown function name is reported back to the model as an error payload
    rather than raising, so a hallucinated tool can't abort the reply.
    """
    if name == "search_theater":
        query = args.get("query", "")
        log.info("Tool call: search_theater(query=%r)", query)
        theaters = search_theaters(query)
        log.info("search_theater returned %d result(s)", len(theaters))
        with st.expander(f"🛠 Searched theaters: {query}", expanded=False):
            if theaters:
                st.dataframe(pd.DataFrame(theaters), width="stretch", hide_index=True)
            else:
                st.caption("No matches.")
        return {"results": theaters}, theaters

    if name == "top_matches":
        n = args.get("n") or 5
        genre = args.get("genre")
        log.info("Tool call: top_matches(n=%r, genre=%r)", n, genre)
        rows = top_matches(ctx.wl_scored, n=n, genre=genre)
        label = f"🛠 Ranked your top matches ({genre})" if genre else "🛠 Ranked your top matches"
        _render_tool_rows(label, rows)
        return {"results": rows}, None

    if name == "showtimes_query":
        title, theater, day = args.get("title"), args.get("theater"), args.get("day")
        log.info("Tool call: showtimes_query(title=%r, theater=%r, day=%r)", title, theater, day)
        rows = showtimes_query(ctx.wl_scored, title=title, theater=theater, day=day)
        criteria = ", ".join(f"{k}={v}" for k, v in (("title", title), ("theater", theater), ("day", day)) if v)
        _render_tool_rows(f"🛠 Searched showtimes: {criteria or 'all upcoming'}", rows)
        return {"results": rows}, None

    log.warning("Ignoring unknown tool call: %r", name)
    return {"error": f"unknown tool {name!r}"}, None


def _render_tool_rows(label: str, rows: list[dict]) -> None:
    """Show a tool's returned rows in a collapsed expander, mirroring ``search_theater``'s UI."""
    with st.expander(label, expanded=False):
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("No matches.")


def _ask_gemini(ctx: ChatContext, history: list[dict]) -> tuple[Iterator[str], list]:
    """Stream a Gemini chat response, handling up to :data:`MAX_TOOL_ROUNDS` rounds of tool use.

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
        tools=[SEARCH_THEATER_TOOL, TASTE_TOOL, SHOWTIMES_TOOL],
        max_output_tokens=settings.gemini_max_tokens,
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
    )
    pending_ref: list[list[dict] | None] = [None]

    def _generate() -> Iterator[str]:
        convo = list(contents)
        theaters: list[dict] | None = None

        # One extra iteration over the round budget: the last pass streams the
        # model's answer to the final tool result without granting a new call.
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            fn_call: types.FunctionCall | None = None
            assistant_parts: list[types.Part] = []

            stream = client.models.generate_content_stream(model=settings.gemini_model, contents=cast(list, convo), config=cfg)
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
                break
            if round_index == MAX_TOOL_ROUNDS:
                log.warning("Tool-call budget of %d round(s) exhausted — ignoring %r", MAX_TOOL_ROUNDS, fn_call.name)
                break

            name = fn_call.name or ""
            payload, tool_theaters = _run_tool(ctx, name, dict(fn_call.args or {}))
            if tool_theaters is not None:
                theaters = tool_theaters

            convo = convo + [
                types.Content(role="model", parts=assistant_parts),
                types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=name or "unknown", response=payload)],
                ),
            ]

        pending_ref[0] = theaters if theaters else None

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
