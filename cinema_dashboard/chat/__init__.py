"""
The Gemini chat assistant, split by responsibility.

- :mod:`chat.prompt` — context assembly (:class:`ChatContext`,
  ``build_chat_context``, the pinned ``build_system_message`` prompt)
- :mod:`chat.state` — conversation state + ``data/chat_state.json`` persistence
- :mod:`chat.tools` — the pure ``top_matches`` / ``showtimes_query`` handlers
- :mod:`chat.transport` — the Gemini round-trip: tool declarations, tool
  dispatch, and the bounded streaming loop (``_ask_gemini``)
- :mod:`chat.pins` — pure pin resolution: which films a reply offers
  (``_find_pinnable_titles``) and which film a pin means (``resolve_pin``)
- :mod:`chat.ui` — ``render_chat`` and the surface it draws

This ``__init__`` deliberately re-exports **nothing**: importing any single
submodule executes the package ``__init__`` first, so a re-export here would
make ``import chat.tools`` pull in ``chat.prompt`` and, through it, the
settings, taste, Allocine and loader layers that the deliberately-leaf
``chat.tools`` has no use for — and would put a cycle one edit away the moment
``chat.prompt`` wanted a helper from ``chat.tools``. Import from the owning
submodule instead (``from chat.prompt import build_chat_context``), matching
the docstring-only ``__init__`` of ``core``, ``sources`` and ``integrations``.
"""
