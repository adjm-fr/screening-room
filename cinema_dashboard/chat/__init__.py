"""
Public re-export surface for the chat package.

``chat.prompt`` (context assembly: :class:`ChatContext`, ``build_chat_context``,
the pinned ``build_system_message`` prompt, ``_streaming_context``) and
``chat.state`` (disk-persisted conversation state: ``CHAT_STATE_PATH``,
:class:`ChatState`, ``chat_state``, ``load_chat_state``, ``save_chat_state``,
``delete_chat_state``) were split out of the original ``utils/chat.py`` module.
Re-exporting their names here is what lets ``pages/recommendations.py``,
``ui/cmdk.py``, ``tests/chat/test_chat.py`` and ``tests/evals/`` keep doing
``from chat import X`` regardless of which of the two modules actually defines
``X``. ``render_chat`` and the LLM transport live in :mod:`chat.ui` and are
not re-exported here — callers that need them import ``chat.ui`` directly.
"""

from chat.prompt import ChatContext, _streaming_context, build_chat_context, build_system_message
from chat.state import CHAT_STATE_PATH, ChatState, chat_state, delete_chat_state, load_chat_state, save_chat_state

__all__ = [
    "CHAT_STATE_PATH",
    "ChatContext",
    "ChatState",
    "build_chat_context",
    "build_system_message",
    "chat_state",
    "delete_chat_state",
    "load_chat_state",
    "save_chat_state",
    "_streaming_context",
]
