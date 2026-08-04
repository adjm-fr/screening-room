"""
Global ``Cmd+K`` command palette.

Mounts a keyboard shortcut and a fallback button that open an ``st.dialog``
hosting the recommendations chat — primary entry point from any page. The
dedicated ``pages/recommendations.py`` page survives for deep history,
prompt chips, the pinned-recs column, and conversation export.

The dialog and the page share ``session_state['chat']`` (a ``ChatState``) so
the conversation persists across both surfaces.

The keyboard binding is a small hand-rolled JS snippet injected via
``st.iframe`` (not the ``streamlit-shortcuts`` package, whose only release
still calls the deprecated ``st.components.v1.html``) — a hidden 1×1 iframe
that binds a ``keydown`` listener on the parent document and clicks the
sidebar button by its ``st-key-*`` class. It is purely additive: the sidebar
button is the fallback entry point if JS ever fails to bind.
"""

from __future__ import annotations

import json
import logging

import streamlit as st

from chat.prompt import build_chat_context
from chat.ui import render_chat

log = logging.getLogger(__name__)

_CMDK_BUTTON_KEY = "_cmdk_btn"
_CMDK_SHORTCUTS = ["ctrl+k", "meta+k"]


def _bind_shortcuts(button_key: str, shortcuts: list[str]) -> None:
    """Bind keyboard ``shortcuts`` to click the button rendered with ``key=button_key``."""
    flag = f"__cmdkShortcutsBound_{button_key}"
    js = f"""<script>
    const doc = window.parent.document;
    const parentWindow = window.parent.window;
    const shortcuts = {json.dumps(shortcuts)};

    // Streamlit re-runs this script on every rerun; guard against re-attaching
    // the listener each time, mirroring streamlit-shortcuts' own approach.
    if (!parentWindow.{flag}) {{
        parentWindow.{flag} = true;
        doc.addEventListener('keydown', (e) => {{
            for (const shortcut of shortcuts) {{
                const parts = shortcut.toLowerCase().split('+');
                const hasCtrl = parts.includes('ctrl');
                const hasMeta = parts.includes('meta') || parts.includes('cmd');
                const mainKey = parts.find((p) => !['ctrl', 'alt', 'shift', 'meta', 'cmd'].includes(p));
                if (hasCtrl !== e.ctrlKey || hasMeta !== e.metaKey || e.key.toLowerCase() !== mainKey) continue;

                e.preventDefault();
                const btn = doc.querySelector('.st-key-{button_key} button');
                if (btn) {{
                    btn.click();
                    btn.focus();
                }}
                return;
            }}
        }});
    }}
    </script>"""
    st.iframe(js, width=1, height=1)


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
    chat dialog, and binds the keyboard shortcut to that same button.
    """
    with st.sidebar:
        if st.button("✦ Ask AI · ⌘K", use_container_width=True, key=_CMDK_BUTTON_KEY):
            _open_palette()

    _bind_shortcuts(_CMDK_BUTTON_KEY, _CMDK_SHORTCUTS)

    if st.session_state.get("_cmdk_open"):
        st.session_state["_cmdk_open"] = False
        _cmdk_dialog()
