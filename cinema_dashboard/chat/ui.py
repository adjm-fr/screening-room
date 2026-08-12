"""
The chat surface: the transcript, the prompt chips, and the pinned column.

Rendering only. The Gemini round-trip lives in :mod:`chat.transport`, the pure
pin-resolution logic in :mod:`chat.pins`, context assembly in :mod:`chat.prompt`
and conversation state in :mod:`chat.state`; this module is what puts them on the
page. It is mounted full-page by ``pages/recommendations.py`` (prompt chips,
pinned-recs column, export) and compact by ``ui/cmdk.py`` (the ``Cmd+K``
``st.dialog``, no pinned column). Both share ``st.session_state["chat"]``, so the
conversation persists across the two surfaces.

Import names from their owning submodule (``from chat.pins import resolve_pin``),
never through ``chat`` itself: the package ``__init__`` deliberately re-exports
nothing, so that ``chat.tools`` stays leaf.

Two HTML builders live here rather than in ``chat.pins`` despite being pure —
:func:`_streaming_caption` and :func:`_pin_caption_html` — following the house
pattern that a markup builder sits beside the renderer that emits it
(``ui.agenda._agenda_row_html``, ``ui.cards._movie_card_html``).

``render_chat`` has no unit tests: it needs a real Streamlit session and is
covered by manual verification.
"""

from __future__ import annotations

import html
import logging
from typing import cast

import pandas as pd
import streamlit as st

from chat.pins import _assistant_text, _find_pinnable_titles, _pin_candidates, _pin_row, resolve_pin
from chat.prompt import ChatContext
from chat.state import ChatState, chat_state, delete_chat_state, save_chat_state
from chat.transport import _ask_gemini
from config import settings
from integrations.theaters import append_theater, load_theater_ids
from sources.loader import coerce_str_list
from sources.streaming import display_name, load_display_names_catalog
from ui import render_compact_movie_card

log = logging.getLogger(__name__)


PROMPT_SUGGESTIONS = [
    "What's playing tonight?",
    "Pick a short film for after work",
    "Surprise me with a Bong Joon-ho-style movie",
    "What can I watch this weekend?",
]


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


def _streaming_caption(pinned: dict) -> str:
    """Escaped ``"📺 Netflix · Arte.tv"`` line, or ``""`` with no providers.

    The fallback for a pin with no screening: a film pinned off the streaming
    block is pinned *because* of where it streams, so a caption-less card
    would drop the one fact the user kept it for. Providers are unfiltered by
    ``STREAMING_SERVICES`` on purpose — the chat names every provider in the
    streaming block, so the caption mirrors what it said.
    """
    providers = [*coerce_str_list(pinned.get("flatrate")), *coerce_str_list(pinned.get("free"))]
    if not providers:
        return ""
    catalogue = load_display_names_catalog()
    return html.escape(f"📺 {' · '.join(display_name(slug, catalogue) for slug in providers)}")


def _pin_caption_html(pinned: dict) -> str:
    """Escaped ``"🎟 Sat 02 Aug · 20:00 — Le Champo"`` line.

    Falls back to the streaming providers (:func:`_streaming_caption`) when the
    pin carries no usable date — a streaming-only pin has no showtime at all —
    and to ``""`` when it carries neither.
    """
    showtime = pinned.get("showtimes")
    if showtime is None or (not isinstance(showtime, str) and pd.isna(showtime)):
        return _streaming_caption(pinned)
    when = pd.to_datetime(showtime, errors="coerce")
    if pd.isna(when):
        return _streaming_caption(pinned)
    theater = pinned.get("theater_name")
    suffix = f" — {theater}" if isinstance(theater, str) and theater else ""
    return html.escape(f"🎟 {when.strftime('%a %d %b · %H:%M')}{suffix}")


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
            candidates = _pin_candidates(ctx)
            state.pinnable = _find_pinnable_titles(_assistant_text(state.messages), *candidates)
            if state.pinnable:
                to_pin = st.multiselect(
                    "Pin from this conversation",
                    options=state.pinnable,
                    key="pin_picker",
                    label_visibility="collapsed",
                )
                if to_pin:
                    existing = {p.get("letterboxd_title") for p in state.pinned_recs}
                    n_before = len(state.pinned_recs)
                    for title in to_pin:
                        if title in existing:
                            continue
                        row = _pin_row(title, *candidates)
                        if row is not None:
                            state.pinned_recs.append(row)
                    if len(state.pinned_recs) > n_before:
                        save_chat_state(state)

            if not state.pinned_recs:
                st.caption("Pinned recommendations will appear here.")
            else:
                for stored in state.pinned_recs:
                    pinned = resolve_pin(stored, ctx.wl_shows, ctx.slug_by_title)
                    render_compact_movie_card(pd.Series(pinned), caption=_pin_caption_html(pinned))
                if st.button("Clear pins", key="clear_pins", use_container_width=True):
                    state.pinned_recs = []
                    # The picker keeps its selection across reruns and the block
                    # above re-pins whatever is still selected, so emptying the
                    # list alone puts every pin straight back on the next run.
                    st.session_state.pop("pin_picker", None)
                    save_chat_state(state)
                    st.rerun()
