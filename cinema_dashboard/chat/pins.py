"""
Pinned recommendations: which films a reply offers, and which film a pin means.

The pure half of the pinning feature, extracted from ``chat.ui`` because that is
what it always was: every function here takes frames and dicts, calls no ``st.``
and no ``google.genai``, and is unit-tested without a session in
``tests/chat/test_pins.py``.

"Pure" means *calls*, not *imports* — ``sources.loader`` is decorated with
``@st.cache_data``, so Streamlit still arrives transitively and always will while
this module reuses the loader's title/director helpers. What the import block
below does buy is measurable: keeping ``ChatContext`` under ``TYPE_CHECKING``
drops this module from 1694 to 1452 imported modules and keeps ``google.genai``
and ``chat.prompt`` out of the graph entirely.

Two rules encoded here are subtle enough that the tests exist to stop them being
simplified away:

- **A title does not identify a film.** 22 titles in the real watchlist name two
  different films (*King Lear* is Brook's *and* Godard's, *Mandy* Mackendrick's
  *and* Cosmatos'), so :func:`resolve_pin` confirms every title match by director
  through ``sources.loader._directors_overlap`` and resolves anything still
  ambiguous to *no* slug — an unlinked pin beats a wrong one.
- **The candidate set is the model's whole closed set.** :func:`_pin_candidates`
  returns the showtimes frame *and* the streamable frame, because scoping it to
  screenings alone made every answer to "what's on Netflix?" unpinnable. Add a new
  source of films the model may name and it must be added there too.

:func:`_find_pinnable_titles` reads the whole transcript rather than the latest
reply, and matches on whole words in *both* title spellings — padding both sides
of the normalized text is what stops a short title (*Up*, *M*, *RRR*, *Ran* inside
"Le Grand Rex") firing on every reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pandas as pd

from sources.loader import _directors_overlap, _normalize_title
from sources.streaming import STREAMING_COLUMNS

if TYPE_CHECKING:  # pragma: no cover
    # Annotation-only: ``_pin_candidates`` reads two frames off a ChatContext but
    # never constructs one. Importing it for real would drag `chat.prompt` — and
    # through it config, core.taste, integrations.allocine and Streamlit — into a
    # module whose whole point is being pure, costing ~500 modules on this hot
    # path for a type name that `from __future__ import annotations` never
    # evaluates. Same instinct as `chat.tools` refusing to take a ChatContext.
    from chat.prompt import ChatContext


def _streamable(streaming_df: pd.DataFrame) -> pd.DataFrame:
    """Return the streaming frame's rows that actually carry a provider.

    Mirrors the filter :func:`chat.prompt._streaming_context` and
    :func:`chat.tools.streaming_query` both apply, so the pin picker offers
    exactly the films the model was allowed to name — no more.
    """
    cols = [c for c in STREAMING_COLUMNS if c in streaming_df.columns]
    if streaming_df.empty or not cols:
        return streaming_df.iloc[0:0]
    has_provider = pd.Series(False, index=streaming_df.index)
    for col in cols:
        has_provider |= streaming_df[col].map(lambda cell: isinstance(cell, list) and bool(cell))
    return streaming_df[has_provider]


def _pin_candidates(ctx: ChatContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The frames a reply's films can be pinned from, in resolution order.

    Together these are the chat's **closed set**: what is screening
    (``wl_shows``, also the frame behind ``top_matches``/``showtimes_query``)
    and what is streaming (the provider-carrying rows of ``streaming_df``, the
    frame behind the streaming block and ``streaming_query``). Anything the
    model may legitimately name is in one of them — which is the point, since
    a film the picker can't offer is a recommendation the user can't keep.
    ``wl_shows`` comes first so a film that is both screening and streaming
    pins with its showtime.
    """
    return ctx.wl_shows, _streamable(ctx.streaming_df)


def _find_pinnable_titles(text: str, *frames: pd.DataFrame) -> list[str]:
    """Return the ``letterboxd_title``s from ``frames`` that appear in ``text``.

    Both title spellings are searched — the original *and* the ``french_title``
    — because the showtimes block feeds the model both and it answers with
    whichever fits (or with both, "Dark Passage (Les Passagers de la nuit)").
    Matching only one spelling left a film unpinnable purely on how the reply
    happened to name it. The canonical ``letterboxd_title`` is always what's
    returned, so the pin key stays stable whichever spelling matched.

    Matching is on **whole words**, not raw substrings: normalization collapses
    everything to space-separated alphanumeric tokens, so padding both sides
    makes ``" ran "`` miss ``"le grand rex"``. That guard is what makes the
    widened candidate set safe — over hundreds of streaming titles, a bare
    substring test fires on every short title (*Up*, *Her*, *M*, *RRR*).
    """
    norm_text = f" {_normalize_title(text)} "
    if not norm_text.strip():
        return []
    matches: set[str] = set()
    for frame in frames:
        if frame.empty or "letterboxd_title" not in frame.columns:
            continue
        cols = [c for c in ("letterboxd_title", "french_title") if c in frame.columns]
        for spellings in frame[cols].drop_duplicates().itertuples(index=False):
            canonical = spellings[0]
            if not isinstance(canonical, str) or not canonical or canonical in matches:
                continue
            if any((norm := _normalize_title(t)) and f" {norm} " in norm_text for t in spellings):
                matches.add(canonical)
    return sorted(matches)


def _assistant_text(messages: list[dict]) -> str:
    """Every assistant reply in the conversation, concatenated.

    The picker is derived from the whole transcript rather than the latest
    reply alone: the earlier replies are still on screen, so the films in them
    are still recommendations the user may want to keep. Deriving it also
    means a conversation reloaded from ``data/chat_state.json`` comes back
    pinnable instead of blank.
    """
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "assistant")


def _pin_row(title: str, *frames: pd.DataFrame) -> dict | None:
    """Return the first row for ``title`` across ``frames``, or ``None``.

    Frames are consulted in order, so a screening row (which carries the
    showtime and theater the caption shows) wins over the streaming row for
    the same film.
    """
    for frame in frames:
        if frame.empty or "letterboxd_title" not in frame.columns:
            continue
        match = frame[frame["letterboxd_title"] == title].head(1)
        if not match.empty:
            return cast(dict, match.iloc[0].to_dict())
    return None


def _confirmed_slug(stored: dict, candidates: list[tuple[str, str]]) -> str | None:
    """Pick the one ``(slug, directors)`` candidate this pin's director confirms.

    A single candidate needs no confirmation — the title is unambiguous. Beyond
    that the title names more than one film (remakes: *King Lear* is both Peter
    Brook's and Godard's), so it is resolved the way
    :func:`sources.loader.build_watchlist_showtimes` resolves the same
    ambiguity — by director, via token containment. Anything that stays
    ambiguous returns ``None``: an unlinked pin is a much smaller failure than
    one that opens the wrong film.
    """
    if len(candidates) == 1:
        return candidates[0][0]
    confirmed = {slug for slug, directors in candidates if _directors_overlap(stored.get("directors"), directors)}
    return confirmed.pop() if len(confirmed) == 1 else None


def resolve_pin(
    stored: dict,
    wl_shows: pd.DataFrame,
    slug_by_title: dict[str, list[tuple[str, str]]] | None = None,
) -> dict:
    """Return the live row behind a stored pin, else the stored dict re-linked.

    Pins are persisted as a whole row snapshot, so a pin taken before a column
    existed keeps that shape forever. That is what left old pins unclickable:
    they predate ``letterboxd_slug`` being carried through the showtimes join,
    so :func:`ui.row_slug` found nothing and the card rendered as plain text.
    Re-resolving at render time rather than migrating the file makes the frozen
    copy only ever a *fallback*, so no future column addition can strand a pin
    again.

    Two levels, because they fix two different failures:

    1. A row from ``wl_shows`` (matched on ``letterboxd_slug``, else on
       ``letterboxd_title`` — the key old pins do carry), taking the **next
       upcoming** screening. Without this a pin keeps advertising whichever
       showtime happened to be scraped the day it was pinned, which goes stale
       within the week.
    2. When the film has no upcoming screenings at all it drops out of
       ``wl_shows`` entirely, so level 1 cannot help. The stored snapshot is
       then returned with a slug attached from ``slug_by_title``, which spans
       the whole watchlist. The detail page reads the cache, not the showtimes,
       so the film still has a page — the pin has no reason to stop linking to
       it just because the run has ended.

    Both levels fall back to matching on **title, which does not identify a
    film** — remakes share one. Every title match is therefore confirmed by
    director (:func:`_confirmed_slug`) and abandoned when it stays ambiguous,
    so a pin never silently opens a different film of the same name.
    """
    slug = stored.get("letterboxd_slug")
    title = stored.get("letterboxd_title")
    has_title = isinstance(title, str) and bool(title)

    match = pd.DataFrame()
    if not wl_shows.empty:
        if isinstance(slug, str) and slug and "letterboxd_slug" in wl_shows.columns:
            match = wl_shows[wl_shows["letterboxd_slug"] == slug]
        if match.empty and has_title and "letterboxd_title" in wl_shows.columns:
            match = _disambiguate_by_director(stored, wl_shows[wl_shows["letterboxd_title"] == title])
    if not match.empty:
        if "showtimes" in match.columns:
            match = match.assign(_dt=pd.to_datetime(match["showtimes"], errors="coerce")).sort_values("_dt").drop(columns=["_dt"])
        return cast(dict, match.iloc[0].to_dict())

    if slug or not slug_by_title or not has_title:
        return stored
    recovered = _confirmed_slug(stored, slug_by_title.get(cast(str, title), []))
    return {**stored, "letterboxd_slug": recovered} if recovered else stored


def _disambiguate_by_director(stored: dict, rows: pd.DataFrame) -> pd.DataFrame:
    """Narrow same-title ``wl_shows`` rows to the one film the pin's director confirms.

    Rows for a single film pass through untouched (the common case). When the
    title spans several films the screenings interleave, so ``.iloc[0]`` after
    sorting by showtime could pick the wrong one; an unconfirmable title
    yields no rows, which drops the caller to the stored snapshot.
    """
    if rows.empty or "letterboxd_slug" not in rows.columns or rows["letterboxd_slug"].nunique() <= 1:
        return rows
    if "directors" not in rows.columns:
        return rows.iloc[0:0]
    confirmed = rows[rows["directors"].map(lambda d: _directors_overlap(stored.get("directors"), d))]
    return confirmed if confirmed["letterboxd_slug"].nunique() == 1 else rows.iloc[0:0]
