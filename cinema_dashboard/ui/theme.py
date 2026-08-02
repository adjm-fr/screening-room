"""
CSS injection and formatting primitives shared by every other ``ui`` module.

The CSS lives in assets/styles.css and is injected on every rerun via
:func:`inject_css`. ``format_runtime`` and ``rating_to_hsl`` are the display
primitives every card/chip helper builds on; ``movie_href``/``row_slug`` are
the movie-detail-link primitives cards and hero cards use to make themselves
clickable.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

_STYLES_PATH = Path(__file__).parent.parent / "assets" / "styles.css"

#: Query-parameter name carrying the Letterboxd slug of the film to detail.
#: Read by ``app.py`` (routing) and written by :func:`movie_href` (every card).
MOVIE_QUERY_PARAM = "movie"

#: Row columns that may hold the Letterboxd slug, in priority order. The
#: watchlist/ratings/cache parquets call it ``slug``; the watchlist↔showtimes
#: join renames it ``letterboxd_slug`` (see
#: :func:`sources.loader.build_watchlist_showtimes`).
_SLUG_COLUMNS = ("slug", "letterboxd_slug")


def inject_css() -> None:
    """Inject ``assets/styles.css`` into the page on every Streamlit rerun.

    Streamlit replaces all rendered output on every rerun, so the ``<style>``
    tag must be re-emitted each time to stay in the DOM.
    """
    try:
        css = _STYLES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("styles.css not found at %s — UI will fall back to Streamlit defaults", _STYLES_PATH)
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ── Formatting helpers ──────────────────────────────────────────────────────


def format_runtime(minutes: int | float | str | None) -> str:
    """Format a runtime in minutes as ``"{h}h{mm}"``.

    Returns ``"—"`` for ``None``, ``NaN``, or zero. If the input is already
    formatted (e.g., "1h 25min"), returns it as-is after stripping whitespace.

    Examples
    --------
    >>> format_runtime(0)
    '—'
    >>> format_runtime(60)
    '1h00'
    >>> format_runtime(132)
    '2h12'
    >>> format_runtime('1h 25min')
    '1h 25min'
    """
    if minutes is None:
        return "—"
    if isinstance(minutes, str):
        minutes = minutes.strip()
        if not minutes:
            return "—"
        if any(c in minutes for c in ("h", "H", "min", "MIN", "hour", "HOUR")):
            return minutes
    try:
        m = int(float(minutes))
    except (ValueError, TypeError):
        return "—"
    if m <= 0:
        return "—"
    hours, rem = divmod(m, 60)
    return f"{hours}h{rem:02d}"


def rating_to_hsl(rating: float | int | None, *, hue: int = 36, scale_max: float = 10.0) -> str:
    """Convert a rating into an ``hsl()`` color string on a saturation heatmap.

    Lightness ramps from 80% (low score) to 40% (high score) at a fixed
    ``hue`` (default 36° amber) and saturation (80%). ``scale_max`` is the top
    of the rating scale (10 for Letterboxd averages, 5 for the user's own
    star rating). Returns ``"transparent"`` for ``None`` or NaN. Always pair the
    resulting color with a numeric label in the UI to satisfy WCAG 1.4.1
    (information not conveyed by color alone).
    """
    if rating is None:
        return "transparent"
    try:
        r = float(rating)
    except (ValueError, TypeError):
        return "transparent"
    if pd.isna(r):
        return "transparent"
    r_clamped = max(0.0, min(scale_max, r))
    lightness = round(80.0 - (r_clamped / scale_max) * 40.0)
    return f"hsl({hue} 80% {lightness}%)"


# ── Movie detail links ──────────────────────────────────────────────────────


def movie_href(slug: str) -> str:
    """Return the relative href of a film's detail page, ``?movie=<slug>``.

    Relative on purpose: it resolves against whatever page the card is
    rendered on, so the same string works from ``/``, ``/database``, ``/calendar``
    — and needs no knowledge of the host or base path. The slug is
    percent-encoded then HTML-escaped, so it is safe to interpolate directly
    into an ``href`` attribute.
    """
    return f"?{MOVIE_QUERY_PARAM}={html.escape(quote(slug, safe=''))}"


def row_slug(row: pd.Series) -> str | None:
    """Return the row's Letterboxd slug from whichever column carries it, else ``None``.

    Tolerates the ``slug``/``letterboxd_slug`` split across frames (see
    :data:`_SLUG_COLUMNS`) and treats NaN/empty as absent, so callers can gate
    link rendering on a single truthiness check.
    """
    for column in _SLUG_COLUMNS:
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
