"""
Cached parquet readers and joins: the watchlist/showtimes/ratings loader,
the streaming-providers cache, and the theater geocoding cache.

No re-exports — call sites import submodules explicitly
(``from sources.loader import build_watchlist_showtimes``,
``from sources.streaming import load_streaming_providers``).

Named ``sources``, not ``data``: ``cinema_dashboard/data/`` is a *runtime*
directory (``data/chat_state.json``, ``data/streaming_providers.parquet``)
listed in both this project's and the workspace root's ``.gitignore`` — a
Python package placed there would be silently untracked and never committed.
"""
