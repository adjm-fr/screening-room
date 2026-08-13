"""
Context assembly and system-prompt construction for the recommendations chat.

:func:`build_chat_context` validates configuration (Gemini key, ``OUTPUT_PATH``,
``ALLOCINE_OUTPUT_PATH``) and on-disk data, loads and joins the
watchlist/showtimes/ratings parquets, and returns a :class:`ChatContext` — the
bundle of taste profile, showtimes/streaming markdown blocks and known
theaters that :func:`build_system_message` renders into the system prompt
anchoring the LLM to a closed set of films/providers (see ``CLAUDE.md``'s
chat-assistant section). Both are called once per Streamlit run, by
``chat.ui``'s callers (``pages/recommendations.py``, ``ui/cmdk.py``).

``streaming_md`` is capped to the top :data:`STREAMING_CONTEXT_TOP_N`
taste-matched films (see :func:`_streaming_context`) — the uncapped block
alone cost ~74% of the system prompt's tokens. ``ChatContext.streaming_df``
carries the full, untruncated frame it was built from so the
``chat.tools.streaming_query`` tool can still reach films the cap left out.

**The system prompt's prose lives in ``assets/system_prompt.md``**, not in this
file: :func:`build_system_message` renders it with :class:`string.Template`. It
moved there because pinned prose written as quoted Python fragments diffs as a
scatter of string literals, and this is the one text that defines the model's
closed set — it should be reviewable as prose. The four values it interpolates
(``$taste``, ``$showtimes_md``, ``$streaming_block``, ``$known_theaters``) are
computed here and arrive as finished strings, so the template holds no logic.
Editing that file, note ``$`` is a metacharacter: a literal one must be ``$$``.

This module owns:
    ChatContext             (the dataclass)
    build_chat_context()   -> ChatContext | None  (config + data validation)
    build_system_message() -> dict                (renders assets/system_prompt.md)
    _streaming_context() / _showtimes_context()    (markdown block builders)

Kept free of any import back into :mod:`chat.ui` or :mod:`chat.state`
— context assembly does not need conversation state or the LLM transport/UI.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from string import Template

import pandas as pd
import streamlit as st

from config import settings
from core.taste import attach_match, build_affinity
from integrations.allocine import _get_paris_cinemas
from integrations.theaters import backfill_addresses, load_theaters
from sources.loader import (
    attach_streaming,
    build_taste_profile,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_ratings,
    load_showtimes,
    load_watchlist,
)

log = logging.getLogger(__name__)

#: The LLM system prompt's prose, rendered by :func:`build_system_message`.
#: Module-level so tests can point at a fixture, matching
#: ``sources.streaming.PROVIDER_DISPLAY_NAMES_PATH``; resolved from this file's
#: location like ``ui.theme``'s stylesheet, and read at call time so the prompt
#: can be edited without restarting the app — one ~6 KB read per turn is free
#: beside the Gemini round-trip it precedes.
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "assets" / "system_prompt.md"

# Cap on how many watchlist films get an inline line in the streaming block.
# The uncapped block cost ~74% of the ~9,500-token system prompt (measured
# against the real cache: 458 lines, ~6,990 tokens); ranking by taste match
# and keeping the top 50 lands the block around 50 lines / ~800 tokens while
# keeping the most-relevant films inline. Anything narrowed out is still
# reachable via the ``streaming_query`` tool (see ``chat.tools``), which
# queries the same underlying frame unfiltered.
STREAMING_CONTEXT_TOP_N = 50


def _gemini_key_configured() -> bool:
    """Return True if the Gemini API key is set, else render an error and return False.

    The Streamlit error is rendered here so both chat surfaces share one message.
    """
    if not settings.gemini_api_key:
        st.error("**GEMINI_API_KEY** is not set in the workspace-root `.env`.")
        return False
    return True


@dataclasses.dataclass
class ChatContext:
    """All data the chat needs once configuration is validated."""

    taste: str
    showtimes_md: str
    streaming_md: str
    known_theaters: list[str]
    theaters_csv: Path | None
    wl_shows: pd.DataFrame
    # ``wl_shows`` with the taste ``match`` column joined on (see
    # ``core.taste.attach_match``) — the frame the ``top_matches`` /
    # ``showtimes_query`` tools query. Falls back to ``wl_shows`` unchanged
    # when there is no usable rating history or scoring fails.
    wl_scored: pd.DataFrame
    # The full watchlist x FR-streaming frame ``streaming_md`` was rendered
    # from, *before* :func:`_streaming_context` truncates it to its top-N
    # lines — the frame the ``streaming_query`` tool queries, so it can reach
    # films the narrowed block left out. Carries the taste ``match`` column
    # when scoring succeeded (same fallback rule as ``wl_scored``).
    streaming_df: pd.DataFrame
    #: Watchlist title -> the ``(slug, directors)`` candidates carrying it, keyed
    #: on both the original and the French title. Covers the *whole* watchlist,
    #: not just the films currently screening, which is what lets
    #: ``chat.ui.resolve_pin`` keep a pinned film linked to its detail page after
    #: its screenings have passed. A list because titles collide across remakes —
    #: see :func:`_slug_by_title`.
    slug_by_title: dict[str, list[tuple[str, str]]]
    n_movies: int
    n_screenings: int


def _streaming_context(wl_shows: pd.DataFrame) -> str:
    """One markdown line per watchlist film with FR streaming availability.

    Empty string when no rows carry streaming data (cache missing or no hits).
    Caller is expected to skip the streaming block in the system prompt when
    this is empty, so the LLM doesn't get distracted by an empty section.

    Line format: ``- {title} — flatrate={a, b}``. This segment is kept
    stable — it's an eval contract (see ``tests/evals/goldens.py``). A
    ``; free={c}`` segment is appended only when the film also has
    free-to-watch providers (Arte.tv, France.tv, …).

    Capped to the top :data:`STREAMING_CONTEXT_TOP_N` films by taste
    ``match`` when ``wl_shows`` carries a ``match`` column (i.e. scoring
    succeeded — see :func:`build_chat_context`); falls back to the full,
    unranked list when it doesn't, since there is no meaningful way to pick a
    "top" subset without a score. When the cap actually drops rows, a
    trailing marker line names the ``streaming_query`` tool that can still
    reach them — deliberately *not* in the pinned ``- {title} —
    flatrate=...`` shape, so it can't be mistaken for a film entry.
    """
    if "flatrate" not in wl_shows.columns:
        return ""
    title_col = "letterboxd_title" if "letterboxd_title" in wl_shows.columns else "french_title"
    ranked_by_match = "match" in wl_shows.columns
    df = wl_shows.sort_values("match", ascending=False, na_position="last") if ranked_by_match else wl_shows
    lines: list[str] = []
    seen: set[str] = set()
    n_eligible = 0
    for _, row in df.iterrows():
        title = row.get(title_col)
        if not isinstance(title, str) or title in seen:
            continue
        flat = row.get("flatrate") if isinstance(row.get("flatrate"), list) else []
        free = row.get("free") if isinstance(row.get("free"), list) else []
        if not flat and not free:
            continue
        seen.add(title)
        n_eligible += 1
        if ranked_by_match and len(lines) >= STREAMING_CONTEXT_TOP_N:
            continue
        line = f"- {title} — flatrate={', '.join(flat)}"
        if free:
            line += f"; free={', '.join(free)}"
        lines.append(line)
    if ranked_by_match and n_eligible > len(lines):
        lines.append(
            f"(+{n_eligible - len(lines)} more watchlist films with streaming availability, not shown here "
            "— call streaming_query to look them up.)"
        )
    return "\n".join(lines)


def _showtimes_context(wl_shows: pd.DataFrame) -> str:
    wanted = [
        "french_title",
        "letterboxd_title",
        "theater_name",
        "showtimes",
        "genres",
        "letterboxd_avg_rating",
        "runtime_minutes",
        "directors",
    ]
    display_cols = [c for c in wanted if c in wl_shows.columns]
    df = wl_shows[display_cols].sort_values("showtimes").drop_duplicates().reset_index(drop=True)
    return df.to_markdown(index=False)


def _slug_by_title(watchlist_df: pd.DataFrame) -> dict[str, list[tuple[str, str]]]:
    """Map every watchlist title spelling to its ``(slug, directors)`` candidates.

    Both ``title`` and ``french_title`` are keyed because a pin's stored title
    can be either, depending on which one the showtimes join matched on.

    The value is a **list**, not a single slug, because a title does not
    identify a film: 22 titles in the real watchlist name two different films
    (*King Lear* is both Peter Brook's and Godard's, *Mandy* both Mackendrick's
    and Cosmatos'). A plain ``dict[str, str]`` would be last-write-wins and
    would silently link a pin to the wrong film — worse than not linking it.
    The director rides along so :func:`chat.ui.resolve_pin` can confirm the
    match the same way the showtimes join does.
    """
    if "slug" not in watchlist_df.columns:
        return {}
    directors_col = watchlist_df["directors"] if "directors" in watchlist_df.columns else None
    mapping: dict[str, list[tuple[str, str]]] = {}
    for column in ("title", "french_title"):
        if column not in watchlist_df.columns:
            continue
        frame = watchlist_df[[column, "slug"]].copy()
        frame["_directors"] = "" if directors_col is None else directors_col.fillna("")
        for title, slug, directors in frame.dropna(subset=[column, "slug"]).itertuples(index=False):
            if not title or not slug:
                continue
            candidates = mapping.setdefault(str(title), [])
            if not any(existing == str(slug) for existing, _ in candidates):
                candidates.append((str(slug), str(directors)))
    return mapping


def build_chat_context() -> ChatContext | None:
    """Load config + data and return a :class:`ChatContext`, or ``None`` if unusable.

    Renders user-friendly Streamlit error messages for missing config or data
    so callers don't have to repeat the boilerplate. Called from both the
    dedicated page and the ``Cmd+K`` dialog.
    """
    movies_path, showtimes_path, theaters_csv = get_paths()

    if not _gemini_key_configured():
        return None
    if not movies_path:
        st.error("**OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return None
    if not showtimes_path:
        st.error("**ALLOCINE_OUTPUT_PATH** is not set in the workspace-root `.env`.")
        return None

    if theaters_csv and "theaters_backfilled" not in st.session_state:
        try:
            log.debug("Backfilling theater addresses from Allocine cache")
            updated = backfill_addresses(theaters_csv, _get_paris_cinemas())
            log.info("Address backfill complete: %d row(s) updated", updated)
        except Exception as exc:
            log.warning("Address backfill failed: %s", exc)
        finally:
            st.session_state.theaters_backfilled = True

    missing: list[str] = []
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists():
        missing.append("watchlist_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not (movies_path / "ratings_with_letterboxd.parquet").exists():
        missing.append("ratings_with_letterboxd.parquet — run `python main.py` in `movies_management`")
    if not showtimes_path.exists():
        missing.append("showtimes.parquet — run `python main.py` in `Allocine-Showtimes-Scraping`")
    if missing:
        for m in missing:
            st.warning(f"Missing: {m}")
        return None

    try:
        ratings_df = load_ratings(str(movies_path))
        watchlist_df = load_watchlist(str(movies_path))
        showtimes_df = load_showtimes(str(showtimes_path))
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return None

    showtimes_df = future_showtimes(showtimes_df)
    wl_shows = build_watchlist_showtimes(showtimes_df, watchlist_df)

    if wl_shows.empty:
        st.info("No upcoming showtimes found for your watchlist movies. Nothing to recommend.")
        return None

    watchlist_streaming = attach_streaming(watchlist_df.rename(columns={"title": "letterboxd_title"}), str(movies_path))

    # Score once here rather than per tool call: both chat surfaces and every
    # turn of the conversation reuse the same frame. Scoring is best-effort —
    # a failure costs the tools (and the streaming block's ranking) their
    # ``match`` column, not the whole page.
    try:
        profile = build_affinity(ratings_df)
        if profile.is_empty:
            wl_scored = wl_shows
            streaming_scored = watchlist_streaming
        else:
            wl_scored = attach_match(wl_shows, watchlist_df, profile)
            streaming_scored = attach_match(watchlist_streaming, watchlist_df, profile)
    except Exception as exc:
        log.warning("Taste scoring failed — chat tools fall back to unscored showtimes: %s", exc)
        wl_scored = wl_shows
        streaming_scored = watchlist_streaming

    showtime_theaters = set(wl_shows["theater_name"].dropna().unique()) if "theater_name" in wl_shows.columns else set()
    csv_theaters = {t["name"] for t in load_theaters(theaters_csv)} if theaters_csv else set()
    known_theaters = sorted(showtime_theaters | csv_theaters)

    return ChatContext(
        taste=build_taste_profile(ratings_df),
        showtimes_md=_showtimes_context(wl_shows),
        streaming_md=_streaming_context(streaming_scored),
        known_theaters=known_theaters,
        theaters_csv=theaters_csv,
        wl_shows=wl_shows,
        wl_scored=wl_scored,
        streaming_df=streaming_scored,
        slug_by_title=_slug_by_title(watchlist_df),
        n_movies=int(wl_shows["letterboxd_title"].nunique()),
        n_screenings=int(len(wl_shows)),
    )


def build_system_message(ctx: ChatContext) -> dict:
    """Build the system message used to anchor the LLM to the provided lists.

    The prose lives in ``assets/system_prompt.md`` and is rendered here with
    :class:`string.Template`. It was moved out of this module because ~80 lines
    of pinned prose written as quoted Python fragments diffs as a scatter of
    string literals, which is a poor way to review a change to the one text that
    defines the model's closed set. The move was gated on byte-equality: the
    rendered output is identical to the concatenation it replaced.

    Extracted as a function (rather than inlined at the call site) so eval tests
    can reproduce the exact system prompt without depending on Streamlit or the
    streaming tool-use loop.

    Two details the next editor needs:

    - **``$`` is a metacharacter in that file.** The prose currently contains
      none; a future price or ``$``-prefixed term must be written ``$$``.
    - **The template holds no logic.** ``streaming_block``'s if/else and the
      theater join stay here and arrive as finished strings, so the file has
      four inert placeholders and nothing to reason about.
    """
    known_theaters_str = "\n".join(f"- {t}" for t in sorted(ctx.known_theaters)) or "None"
    streaming_block = (
        f"\nFR streaming availability for watchlist films (TMDB / JustWatch):\n{ctx.streaming_md}\n"
        if ctx.streaming_md
        else (
            "\nFR streaming availability for watchlist films: NONE — "
            "no watchlist films are currently available on any streaming service.\n"
        )
    )
    # `.substitute`, never `.safe_substitute`: it raises on a missing key or an
    # unknown $placeholder, so a typo fails on the first turn instead of shipping
    # a prompt with a literal "$taste" in it. A missing file is likewise allowed
    # to raise — unlike `ui.theme.inject_css`, where absent CSS costs only
    # styling, an absent system prompt silently ungrounds the model and voids the
    # closed-set guarantee the whole chat design rests on.
    # Exactly one trailing newline is removed. The file has to end with one —
    # `end-of-file-fixer` in the pre-commit hooks enforces that on every text
    # file in the repo — but the prompt it renders must not, or it drifts one
    # byte from the string concatenation this replaced. Stripping one newline
    # (not `.rstrip`) keeps both true and leaves a deliberate blank line intact.
    raw = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    template = Template(raw[:-1] if raw.endswith("\n") else raw)
    return {
        "role": "system",
        "content": template.substitute(
            taste=ctx.taste,
            showtimes_md=ctx.showtimes_md,
            streaming_block=streaming_block,
            known_theaters=known_theaters_str,
        ),
    }
