"""
Reusable chat renderer for the cinema recommendations assistant.

The chat is mounted in two places:

- ``pages/recommendations.py`` — the dedicated full-page surface (with prompt
  chips, pinned recommendations column, conversation export)
- ``ui/cmdk.py`` — the global ``Cmd+K`` dialog (compact variant, no pinned
  column, shares the same conversation state via Streamlit ``session_state``)

Both surfaces share a single ``session_state['chat']`` (a :class:`ChatState`
dataclass, defined in :mod:`chat.state`) so the conversation persists
across them; that module also owns the ``data/chat_state.json`` disk
persistence. Context assembly — :class:`ChatContext`, ``build_chat_context``,
and the pinned ``build_system_message`` prompt — lives in
:mod:`chat.prompt`. Callers import each name from its owning submodule; the
package ``__init__`` re-exports nothing, so that importing the leaf
:mod:`chat.tools` cannot drag this transport's dependencies in with it.

This module itself owns the LLM transport and the UI:
    render_chat(ctx, ...) -> None      (the UI: history, streaming reply, pins)
    PROMPT_SUGGESTIONS    : list[str]  (chip-row examples)

The pin picker offers films from the *whole* closed set the model may name —
what is screening **and** what is streaming (:func:`_pin_candidates`) — matched
against the *whole* transcript rather than the latest reply
(:func:`_assistant_text`). Both halves are load-bearing: scoping it to
``wl_shows`` made every streaming-only recommendation unpinnable, and scoping
it to the last reply un-pinned earlier ones the moment a follow-up was asked.

The Gemini API call lives in :func:`_ask_gemini`, which streams the assistant
reply and handles up to :data:`MAX_TOOL_ROUNDS` rounds of tool use, dispatched
by :func:`_run_tool`: ``search_theater`` (Allocine lookup, defined here) plus
``top_matches`` / ``showtimes_query`` / ``streaming_query`` (pure queries over
the injected data, defined in :mod:`chat.tools`). A round may carry *several*
parallel calls — one question can name several theaters — and every one of them
is executed and answered. ``_run_tool`` and
``_render_tool_rows`` render Streamlit expanders (transport-with-UI), which is
why they stay here beside ``_ask_gemini`` rather than in the Streamlit-free
``chat.tools``.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from typing import cast

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types

from chat.prompt import ChatContext, build_system_message
from chat.state import ChatState, chat_state, delete_chat_state, save_chat_state
from chat.tools import SHOWTIMES_TOOL, STREAMING_TOOL, TASTE_TOOL, showtimes_query, streaming_query, top_matches
from config import settings
from integrations.allocine import search_theaters
from integrations.theaters import append_theater, load_theater_ids
from sources.loader import _directors_overlap, _normalize_title, coerce_str_list
from sources.streaming import STREAMING_COLUMNS, display_name, load_display_names_catalog
from ui import render_compact_movie_card

log = logging.getLogger(__name__)


PROMPT_SUGGESTIONS = [
    "What's playing tonight?",
    "Pick a short film for after work",
    "Surprise me with a Bong Joon-ho-style movie",
    "What can I watch this weekend?",
]

SEARCH_THEATER_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_theater",
            description=(
                "Search Allocine for Paris cinemas matching a name. "
                "Call this whenever the user names or asks about any theater that is NOT in the known "
                "theaters list — including plain membership questions like 'is X in the list?', 'do you "
                "know the X cinema?', or 'what about X?'. Always call the tool instead of answering from "
                "the known list; never tell the user the theater is unknown or ask whether to search — "
                "just search. The tool takes ONE theater per call: when the user names several in the "
                "same question, issue one call per theater in that same turn, never a single call "
                "joining them."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The theater's distinctive name only, e.g. 'Brady' — strip generic words "
                            "like 'cinema', 'theater', 'the', so it substring-matches the Allocine name."
                        ),
                    )
                },
                required=["query"],
            ),
        )
    ]
)


def _history_to_contents(history: list[dict]) -> list[types.Content]:
    """Map OpenAI-style chat history (``role`` in ``{user, assistant}``) to Gemini ``Content``s.

    Gemini uses ``"model"`` where OpenAI uses ``"assistant"``. Only text turns
    are stored in ``rec_messages`` — tool exchanges are added inline in
    :func:`_ask_gemini` and not persisted to history.
    """
    contents: list[types.Content] = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))
    return contents


# How many *rounds* of tool use one user turn may trigger. Bounded so a model
# that keeps asking for tools can't loop forever (or drain the token budget);
# the reply is still streamed, the surplus round is simply ignored. A single
# round may carry several parallel calls (see :func:`_ask_gemini`), so this
# budget limits chained follow-ups, not how many theaters one turn can look up.
MAX_TOOL_ROUNDS = 2


def _run_tool(ctx: ChatContext, name: str, args: dict) -> tuple[dict, list[dict] | None]:
    """Execute one tool call and surface it in the UI as a transparent expander.

    Returns ``(response_payload, theater_results)``: the payload goes back to
    Gemini as the function response, and ``theater_results`` is non-``None``
    only for ``search_theater`` — the one tool whose output also drives the
    "add this theater?" confirmation flow (:func:`_render_pending_theaters`).

    An unknown function name is reported back to the model as an error payload
    rather than raising, so a hallucinated tool can't abort the reply.
    """
    if name == "search_theater":
        query = args.get("query", "")
        log.info("Tool call: search_theater(query=%r)", query)
        theaters = search_theaters(query)
        log.info("search_theater returned %d result(s)", len(theaters))
        with st.expander(f"🛠 Searched theaters: {query}", expanded=False):
            if theaters:
                st.dataframe(pd.DataFrame(theaters), width="stretch", hide_index=True)
            else:
                st.caption("No matches.")
        return {"results": theaters}, theaters

    if name == "top_matches":
        n = args.get("n") or 5
        genre = args.get("genre")
        log.info("Tool call: top_matches(n=%r, genre=%r)", n, genre)
        rows = top_matches(ctx.wl_scored, n=n, genre=genre)
        label = f"🛠 Ranked your top matches ({genre})" if genre else "🛠 Ranked your top matches"
        _render_tool_rows(label, rows)
        return {"results": rows}, None

    if name == "showtimes_query":
        title, theater, day = args.get("title"), args.get("theater"), args.get("day")
        log.info("Tool call: showtimes_query(title=%r, theater=%r, day=%r)", title, theater, day)
        rows = showtimes_query(ctx.wl_scored, title=title, theater=theater, day=day)
        criteria = ", ".join(f"{k}={v}" for k, v in (("title", title), ("theater", theater), ("day", day)) if v)
        _render_tool_rows(f"🛠 Searched showtimes: {criteria or 'all upcoming'}", rows)
        return {"results": rows}, None

    if name == "streaming_query":
        title, provider = args.get("title"), args.get("provider")
        log.info("Tool call: streaming_query(title=%r, provider=%r)", title, provider)
        rows = streaming_query(ctx.streaming_df, title=title, provider=provider)
        criteria = ", ".join(f"{k}={v}" for k, v in (("title", title), ("provider", provider)) if v)
        _render_tool_rows(f"🛠 Searched streaming: {criteria or 'all'}", rows)
        return {"results": rows}, None

    log.warning("Ignoring unknown tool call: %r", name)
    return {"error": f"unknown tool {name!r}"}, None


def _merge_theaters(seen: list[dict] | None, new: list[dict]) -> list[dict]:
    """Accumulate ``search_theater`` results across several calls in one turn.

    A multi-theater question ("do you know the Brady and the Champo?") produces
    one call per theater, and the "add this theater?" flow must offer all of
    them, not just the last. Deduped on Allocine id because
    :func:`_render_pending_theaters` keys its Add buttons on it — a repeat would
    raise a duplicate-widget-key error.
    """
    merged = list(seen or [])
    known = {t["id"] for t in merged}
    for theater in new:
        if theater["id"] not in known:
            known.add(theater["id"])
            merged.append(theater)
    return merged


def _render_tool_rows(label: str, rows: list[dict]) -> None:
    """Show a tool's returned rows in a collapsed expander, mirroring ``search_theater``'s UI."""
    with st.expander(label, expanded=False):
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.caption("No matches.")


def _ask_gemini(ctx: ChatContext, history: list[dict]) -> tuple[Iterator[str], list]:
    """Stream a Gemini chat response, handling up to :data:`MAX_TOOL_ROUNDS` rounds of tool use.

    Returns ``(text_stream, pending_ref)`` where ``pending_ref`` is a
    single-element list populated *after* the generator is exhausted with the
    list of theater suggestions awaiting user confirmation (or ``None``).
    """
    log.debug("Calling Gemini API — model: %s, history length: %d messages", settings.gemini_model, len(history))
    client = genai.Client(api_key=settings.gemini_api_key)
    system_instruction = build_system_message(ctx)["content"]
    contents = _history_to_contents(history)
    cfg = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[SEARCH_THEATER_TOOL, TASTE_TOOL, SHOWTIMES_TOOL, STREAMING_TOOL],
        max_output_tokens=settings.gemini_max_tokens,
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
    )
    pending_ref: list[list[dict] | None] = [None]

    def _generate() -> Iterator[str]:
        convo = list(contents)
        theaters: list[dict] | None = None

        # One extra iteration over the round budget: the last pass streams the
        # model's answer to the final tool result without granting a new call.
        for round_index in range(MAX_TOOL_ROUNDS + 1):
            fn_calls: list[types.FunctionCall] = []
            assistant_parts: list[types.Part] = []

            stream = client.models.generate_content_stream(model=settings.gemini_model, contents=cast(list, convo), config=cfg)
            for chunk in stream:
                if not chunk.candidates or chunk.candidates[0].content is None:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    if part.text:
                        assistant_parts.append(part)
                        yield part.text
                    elif part.function_call:
                        fn_calls.append(part.function_call)
                        assistant_parts.append(part)

            if not fn_calls:
                break
            if round_index == MAX_TOOL_ROUNDS:
                log.warning(
                    "Tool-call budget of %d round(s) exhausted — ignoring %s",
                    MAX_TOOL_ROUNDS,
                    [c.name for c in fn_calls],
                )
                break

            # Every call in the turn must be answered: Gemini rejects a turn whose
            # function responses don't cover its function calls one-for-one.
            response_parts: list[types.Part] = []
            for fn_call in fn_calls:
                name = fn_call.name or ""
                payload, tool_theaters = _run_tool(ctx, name, dict(fn_call.args or {}))
                if tool_theaters is not None:
                    theaters = _merge_theaters(theaters, tool_theaters)
                response_parts.append(types.Part.from_function_response(name=name or "unknown", response=payload))

            convo = convo + [
                types.Content(role="model", parts=assistant_parts),
                types.Content(role="user", parts=response_parts),
            ]

        pending_ref[0] = theaters if theaters else None

    return _generate(), pending_ref


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


def _render_pending_theaters(ctx: ChatContext) -> None:
    state = chat_state()
    if not state.pending_theaters or not ctx.theaters_csv:
        return
    st.divider()
    st.markdown("**Found these Paris theaters — add one to your list?**")
    for theater in state.pending_theaters:
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{theater['name']}** — {theater.get('address', '')}")
        if col2.button("Add", key=f"add_{theater['id']}"):
            added = append_theater(ctx.theaters_csv, theater["id"], theater["name"], theater.get("address", ""))
            if added:
                st.success(f"Added **{theater['name']}**. Re-run the Allocine scraper to fetch its showtimes.")
            else:
                st.info(f"**{theater['name']}** is already in your theater list.")
            state.pending_theaters = None
            st.rerun()
    if st.button("Dismiss", key="dismiss_pending"):
        state.pending_theaters = None
        st.rerun()
    st.divider()


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


def _streaming_caption(pinned: dict) -> str:
    """Escaped ``"📺 Netflix · Arte.tv"`` line, or ``""`` with no providers.

    The fallback for a pin with no screening: a film pinned off the streaming
    block is pinned *because* of where it streams, so a caption-less card
    would drop the one fact the user kept it for. Providers are unfiltered by
    ``STREAMING_SERVICES`` on purpose — the chat names every provider in the
    streaming block, so the caption mirrors what it said.
    """
    providers = [*coerce_str_list(pinned.get("flatrate")), *coerce_str_list(pinned.get("free"))]
    if not providers:
        return ""
    catalogue = load_display_names_catalog()
    return html.escape(f"📺 {' · '.join(display_name(slug, catalogue) for slug in providers)}")


def _pin_caption_html(pinned: dict) -> str:
    """Escaped ``"🎟 Sat 02 Aug · 20:00 — Le Champo"`` line.

    Falls back to the streaming providers (:func:`_streaming_caption`) when the
    pin carries no usable date — a streaming-only pin has no showtime at all —
    and to ``""`` when it carries neither.
    """
    showtime = pinned.get("showtimes")
    if showtime is None or (not isinstance(showtime, str) and pd.isna(showtime)):
        return _streaming_caption(pinned)
    when = pd.to_datetime(showtime, errors="coerce")
    if pd.isna(when):
        return _streaming_caption(pinned)
    theater = pinned.get("theater_name")
    suffix = f" — {theater}" if isinstance(theater, str) and theater else ""
    return html.escape(f"🎟 {when.strftime('%a %d %b · %H:%M')}{suffix}")


def render_chat(ctx: ChatContext, *, show_prompt_chips: bool = True, show_pinned_column: bool = True) -> None:
    """Render the chat UI: prompt chips, history, streaming response, pending theaters.

    When ``show_pinned_column`` is True (page surface), the chat occupies a
    2/3 column with pinned recommendations on the right. When False (dialog
    surface), the chat fills the available width.
    """
    state = chat_state()

    if show_pinned_column:
        chat_col, pinned_col = st.columns([2, 1])
    else:
        chat_col = st.container()
        pinned_col = None

    with chat_col:
        st.caption(f"Model: `{settings.gemini_model}` · {ctx.n_movies} watchlist movies · {ctx.n_screenings} upcoming screenings")

        if show_prompt_chips and not state.messages:
            chosen = st.pills(
                "Try a prompt",
                options=PROMPT_SUGGESTIONS,
                selection_mode="single",
                key="prompt_chips",
            )
            if chosen and state.last_chip != chosen:
                state.last_chip = chosen
                state.pending_prompt = chosen
                st.rerun()

        _render_pending_theaters(ctx)

        for msg in state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        queued = state.pending_prompt
        state.pending_prompt = None
        prompt = queued or st.chat_input("Ask about what's showing…")
        if prompt:
            state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.status("Thinking…", expanded=False) as status:
                    try:
                        stream, pending_ref = _ask_gemini(ctx, state.messages)
                    except Exception as exc:
                        log.exception("Gemini API call failed")
                        reply: str = f"API error: {exc}"
                        pending: list[dict] | None = None
                        st.markdown(reply)
                        status.update(label="Failed", state="error")
                    else:
                        reply = cast(str, st.write_stream(stream))
                        pending = pending_ref[0]
                        status.update(label="Done", state="complete")

            state.messages.append({"role": "assistant", "content": reply})
            save_chat_state(state)

            if pending and ctx.theaters_csv:
                existing_ids = load_theater_ids(ctx.theaters_csv)
                new_pending = [t for t in pending if t["id"] not in existing_ids]
                state.pending_theaters = new_pending if new_pending else None
            st.rerun()

        if state.messages:
            c1, c2 = st.columns(2)
            with c1:
                conv_md = "\n\n".join(f"### {m['role'].title()}\n\n{m['content']}" for m in state.messages)
                st.download_button(
                    "💾 Save conversation",
                    data=conv_md.encode("utf-8"),
                    file_name="recommendations_conversation.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
            with c2:
                if st.button("🗑 Clear conversation", use_container_width=True):
                    st.session_state["chat"] = ChatState()
                    delete_chat_state()
                    st.rerun()

    if pinned_col is not None:
        with pinned_col:
            st.markdown("##### 📌 Pinned")
            candidates = _pin_candidates(ctx)
            state.pinnable = _find_pinnable_titles(_assistant_text(state.messages), *candidates)
            if state.pinnable:
                to_pin = st.multiselect(
                    "Pin from this conversation",
                    options=state.pinnable,
                    key="pin_picker",
                    label_visibility="collapsed",
                )
                if to_pin:
                    existing = {p.get("letterboxd_title") for p in state.pinned_recs}
                    n_before = len(state.pinned_recs)
                    for title in to_pin:
                        if title in existing:
                            continue
                        row = _pin_row(title, *candidates)
                        if row is not None:
                            state.pinned_recs.append(row)
                    if len(state.pinned_recs) > n_before:
                        save_chat_state(state)

            if not state.pinned_recs:
                st.caption("Pinned recommendations will appear here.")
            else:
                for stored in state.pinned_recs:
                    pinned = resolve_pin(stored, ctx.wl_shows, ctx.slug_by_title)
                    render_compact_movie_card(pd.Series(pinned), caption=_pin_caption_html(pinned))
                if st.button("Clear pins", key="clear_pins", use_container_width=True):
                    state.pinned_recs = []
                    # The picker keeps its selection across reruns and the block
                    # above re-pins whatever is still selected, so emptying the
                    # list alone puts every pin straight back on the next run.
                    st.session_state.pop("pin_picker", None)
                    save_chat_state(state)
                    st.rerun()
