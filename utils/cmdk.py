"""
Global ``Cmd+K`` command palette.

Mounts a keyboard shortcut and a fallback button that open an ``st.dialog``
hosting the recommendations chat — primary entry point from any page. The
dedicated ``pages/recommendations.py`` page survives for deep history,
prompt chips, the pinned-recs column, and conversation export.

The dialog and the page share ``session_state['rec_messages']`` so the
conversation persists across both surfaces.

Keyboard binding uses ``streamlit-shortcuts`` when available; otherwise the
sidebar button is the only entry. The behaviour is identical either way —
the shortcut is purely additive.
"""

from __future__ import annotations

import logging

import streamlit as st
import streamlit_shortcuts  # type: ignore[import-untyped]

from utils.chat import build_chat_context, render_chat

log = logging.getLogger(__name__)


@st.dialog("✦ Ask the cinema assistant", width="large")
def _cmdk_dialog() -> None:
    ctx = build_chat_context()
    if ctx is None:
        return
    render_chat(ctx, show_prompt_chips=True, show_pinned_column=False)


def _open_palette() -> None:
    st.session_state["_cmdk_open"] = True


def mount_cmdk() -> None:
    """Mount the global command-palette button + ``Cmd+K`` / ``Ctrl+K`` shortcut.

    Renders an "✦ Ask AI" button at the top of the sidebar that opens the
    chat dialog, and binds the keyboard shortcut to that same button via
    ``streamlit-shortcuts``.
    """
    with st.sidebar:
        if st.button("✦ Ask AI · ⌘K", use_container_width=True, key="_cmdk_btn"):
            _open_palette()

    streamlit_shortcuts.add_shortcuts(_cmdk_btn=["ctrl+k", "meta+k"])

    if st.session_state.get("_cmdk_open"):
        st.session_state["_cmdk_open"] = False
        _cmdk_dialog()
