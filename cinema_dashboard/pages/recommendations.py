"""
Recommendations page.

Mounts the cinema chat assistant with the full power-user surface:
- prompt-suggestion chips
- streaming spinner with transparent tool-call expanders
- in-page pinned-recommendations column on the right
- conversation export as Markdown

The same assistant is reachable from any other page via the global
``Cmd+K`` palette (see :mod:`utils.cmdk`); both surfaces share
``st.session_state['chat']`` (a ``ChatState``) so the conversation persists
across them.
"""

from __future__ import annotations

import streamlit as st

from utils.chat import build_chat_context, render_chat


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2rem;">Recommendations</h1>', unsafe_allow_html=True)
    st.caption("Ask about watchlist movies that are currently showing, or pin picks from the chat.")

    ctx = build_chat_context()
    if ctx is None:
        return
    render_chat(ctx, show_prompt_chips=True, show_pinned_column=True)


main()
