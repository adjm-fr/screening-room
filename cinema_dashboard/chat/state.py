"""
Conversation state and disk persistence for the recommendations chat.

``ChatState`` is the single dataclass shared by both chat surfaces —
``pages/recommendations.py`` (the dedicated full-page surface) and
``ui/cmdk.py`` (the global ``Cmd+K`` dialog) — via
``st.session_state['chat']``, so the conversation persists across them. The
transcript and pinned recommendations are additionally persisted to
``data/chat_state.json`` (:data:`CHAT_STATE_PATH`, gitignored beside the
streaming/geo caches) so they survive app restarts — loaded on first session
access, saved after each assistant turn and pin change, deleted by
"Clear conversation".

This module owns:
    ChatState             (the dataclass)
    CHAT_STATE_PATH        : Path                       (on-disk location)
    save_chat_state() / load_chat_state() / delete_chat_state()  (disk persistence)
    chat_state()          -> ChatState                  (session accessor)

Consumed by :mod:`chat.ui` (the UI) and :mod:`chat.prompt` is
deliberately independent of this module — context assembly does not need
conversation state.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import cast

import streamlit as st

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
    #: The pin picker's options, *derived* from ``messages`` on every render of
    #: the pinned column (``chat.ui._find_pinnable_titles``) rather than
    #: accumulated turn by turn — so a transcript reloaded from disk is
    #: pinnable again, and no turn can drop an earlier turn's films.
    pinnable: list[str] = dataclasses.field(default_factory=list)
    last_chip: str | None = None
    pending_prompt: str | None = None


# On-disk snapshot of the conversation (transcript + pinned recommendations),
# stored in the gitignored ``data/`` dir beside the streaming/geo caches.
# Module-level so tests can patch it (same pattern as
# ``sources.streaming.PROVIDER_DISPLAY_NAMES_PATH``); the helpers below resolve
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
