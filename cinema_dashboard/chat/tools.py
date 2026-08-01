"""
Pure data-query handlers behind the Gemini chat's taste/showtime tools.

Two tools live here, beside ``chat.ui``'s ``search_theater``:

- ``top_matches``     — the user's own watchlist films ranked by taste match
- ``showtimes_query`` — targeted showtime lookup (title / theater / day)

**CLOSED-SET INVARIANT.** Both handlers are *filters*, never generators: every
row they return is drawn from the DataFrame they are passed (``ChatContext.
wl_scored``, i.e. the same watchlist×showtimes data already injected into the
system prompt). They never synthesize a film, theater, provider or showtime,
and they never reach outside the frame — no parquet reads, no network, no
model knowledge. Any tool added here must preserve that by construction, so
the LLM's closed set stays exactly the injected context (see ``CLAUDE.md``).

The handlers deliberately take the **DataFrame**, not ``ChatContext``: that
type lives in ``chat.ui``, which imports this module, so taking the frame
keeps this module import-cycle-free, Streamlit-free and directly unit-testable.
``chat.ui``'s dispatch passes ``ctx.wl_scored``.

Both handlers are total: an empty frame, missing columns, NaN cells or junk
arguments yield ``[]`` rather than an exception — a tool that raises would
abort the assistant's reply mid-stream.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from google.genai import types

from sources.loader import _normalize_title

log = logging.getLogger(__name__)

# Upper bounds on what a single tool call may put back into the prompt: a
# broad query ("everything at the MK2") must not blow up the context window.
MAX_TOP_MATCHES = 20
MAX_SHOWTIME_ROWS = 20

TASTE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="top_matches",
            description=(
                "Rank the user's OWN watchlist films that have upcoming screenings by how well they fit "
                "their personal taste profile (a 0-100 match score derived from their Letterboxd ratings). "
                "Call this whenever the user asks what they would most enjoy — 'what are my top matches "
                "tonight?', 'what should I prioritise?', 'best pick for me', 'anything great in my "
                "watchlist?'. Returns only films already present in the provided showtimes data, never "
                "outside suggestions."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "n": types.Schema(
                        type=types.Type.INTEGER,
                        description=f"How many films to return (default 5, capped at {MAX_TOP_MATCHES}).",
                    ),
                    "genre": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Optional genre to restrict the ranking to, e.g. 'Drama' or 'horror'. "
                            "Matched case-insensitively as a substring of each film's genre list; omit "
                            "for an unrestricted ranking."
                        ),
                    ),
                },
                required=[],
            ),
        )
    ]
)

SHOWTIMES_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="showtimes_query",
            description=(
                "Look up upcoming screenings of the user's watchlist films, filtered by film title, "
                "theater and/or day. Call this for targeted showtime questions — 'when is X playing?', "
                "'what's on at the Champo on Saturday?', 'anything on 2026-07-25?' — instead of scanning "
                "the showtimes table by eye. All filters are optional and combine with AND; results are "
                f"the soonest screenings first, capped at {MAX_SHOWTIME_ROWS} rows."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Optional film title (or a distinctive part of it). Matched "
                            "accent/case-insensitively as a substring against both the original and the "
                            "French title."
                        ),
                    ),
                    "theater": types.Schema(
                        type=types.Type.STRING,
                        description="Optional theater name, matched case-insensitively as a substring, e.g. 'Champo'.",
                    ),
                    "day": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Optional calendar day, as an ISO date: 'YYYY-MM-DD' (e.g. '2026-07-25'). "
                            "Resolve relative wording like 'tonight' or 'this Saturday' to an ISO date "
                            "yourself before calling; an unparseable value is ignored (the results then "
                            "span every upcoming day, and each row states its own date)."
                        ),
                    ),
                },
                required=[],
            ),
        )
    ]
)


def _title_column(df: pd.DataFrame) -> str | None:
    """Return the column holding the display title, preferring the original over the French one."""
    for col in ("letterboxd_title", "french_title"):
        if col in df.columns:
            return col
    return None


def _clean(value: object) -> Any:
    """Coerce one cell to a small JSON-serializable value (``None`` for null/NaN).

    Timestamps become ``"Fri 25 Jul · 20:00"``-style strings and numpy scalars
    become Python floats/ints, because the result is handed back to Gemini as a
    function response and must survive JSON serialization.
    """
    if value is None or (not isinstance(value, (list, tuple)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    return str(value)


def _row_entry(row: pd.Series, title_col: str | None) -> dict:
    """Project one showtime row down to the small dict the model sees."""
    entry: dict[str, Any] = {"title": _clean(row.get(title_col)) if title_col else None}
    for key, col in (
        ("match", "match"),
        ("directors", "directors"),
        ("genres", "genres"),
        ("theater", "theater_name"),
        ("showtime", "showtimes"),
    ):
        value = _clean(row.get(col))
        if value is not None:
            entry[key] = value
    return entry


def top_matches(wl_scored: pd.DataFrame, *, n: int = 5, genre: str | None = None) -> list[dict]:
    """Return the top-``n`` taste-matched watchlist films with upcoming screenings.

    ``wl_scored`` is one row per film×showtime, so results are deduplicated by
    title — each film appears once, carrying its *soonest* screening (rows are
    ordered by match descending, showtime ascending before the dedupe).

    ``genre`` is matched case-insensitively as a substring of the ``genres``
    cell. When ``genre`` is given but the frame carries no ``genres`` column the
    filter cannot be honoured, so the result is empty rather than a silently
    unfiltered ranking the model would present as genre-matched.

    Returns ``[]`` — never raises — for an empty frame, absent columns or any
    unexpected argument value.
    """
    try:
        if wl_scored is None or wl_scored.empty:
            return []
        try:
            limit = max(1, min(int(n), MAX_TOP_MATCHES))
        except (TypeError, ValueError):
            limit = 5

        df = wl_scored
        if genre:
            if "genres" not in df.columns:
                log.info("top_matches(genre=%r): no 'genres' column — returning no rows", genre)
                return []
            needle = genre.strip().lower()
            df = df[df["genres"].map(lambda cell: isinstance(cell, str) and needle in cell.lower())]
        if df.empty:
            return []

        # Match descending, showtime ascending: the dedupe below keeps the
        # first row per film, i.e. its soonest screening.
        sort_cols = [c for c in ("match", "showtimes") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols, ascending=[c == "showtimes" for c in sort_cols], na_position="last")

        title_col = _title_column(df)
        if title_col is not None:
            df = df.drop_duplicates(subset=[title_col])
        return [_row_entry(row, title_col) for _, row in df.head(limit).iterrows()]
    except Exception:  # a raising tool would abort the assistant reply mid-stream
        log.exception("top_matches failed — returning no rows")
        return []


def showtimes_query(
    wl_scored: pd.DataFrame,
    *,
    title: str | None = None,
    theater: str | None = None,
    day: str | None = None,
) -> list[dict]:
    """Return upcoming screenings filtered by ``title``, ``theater`` and/or ``day``.

    ``title`` is matched as a substring of the normalized (accent- and
    case-folded, see :func:`sources.loader._normalize_title`) original *and*
    French titles; ``theater`` as a case-insensitive substring of
    ``theater_name``; ``day`` against the calendar date of ``showtimes``.

    ``day`` is parsed with ``pd.to_datetime(..., errors="coerce")``. A value
    that fails to parse (``NaT`` — e.g. the model passed "tonight" instead of
    an ISO date) drops the day filter instead of raising or returning nothing:
    every returned row carries its own ``showtime`` date, so a wider result set
    cannot be misread as "that day", whereas an empty one would read as "no
    screenings that day" and mislead the user.

    A filter whose column is absent from the frame yields ``[]`` — the filter
    cannot be honoured and unfiltered rows would misrepresent the query.
    Results are soonest-first and capped at :data:`MAX_SHOWTIME_ROWS`.
    """
    try:
        if wl_scored is None or wl_scored.empty:
            return []
        df = wl_scored

        if title:
            title_cols = [c for c in ("letterboxd_title", "french_title") if c in df.columns]
            if not title_cols:
                log.info("showtimes_query(title=%r): no title column — returning no rows", title)
                return []
            needle = _normalize_title(title)
            if needle:
                mask = pd.Series(False, index=df.index)
                for col in title_cols:
                    mask |= df[col].map(lambda cell: needle in _normalize_title(cell))
                df = df[mask]

        if theater:
            if "theater_name" not in df.columns:
                log.info("showtimes_query(theater=%r): no 'theater_name' column — returning no rows", theater)
                return []
            needle_theater = theater.strip().lower()
            df = df[df["theater_name"].map(lambda cell: isinstance(cell, str) and needle_theater in cell.lower())]

        when = pd.to_datetime(day, errors="coerce") if day else pd.NaT
        if not pd.isna(when):
            if "showtimes" not in df.columns:
                log.info("showtimes_query(day=%r): no 'showtimes' column — returning no rows", day)
                return []
            df = df[pd.to_datetime(df["showtimes"], errors="coerce").dt.date == when.date()]
        elif day:
            log.info("showtimes_query: unparseable day=%r — day filter ignored", day)

        if df.empty:
            return []
        if "showtimes" in df.columns:
            df = df.sort_values("showtimes", na_position="last")
        title_col = _title_column(df)
        return [_row_entry(row, title_col) for _, row in df.head(MAX_SHOWTIME_ROWS).iterrows()]
    except Exception:  # a raising tool would abort the assistant reply mid-stream
        log.exception("showtimes_query failed — returning no rows")
        return []
