"""
The Gemini round-trip: the tool declarations, tool dispatch, and the streaming call.

Split out of ``chat.ui`` so the conversation's *transport* is readable apart from
its *rendering*. What lives here is everything between a user's message and the
model's reply: turning history into ``types.Content``, declaring the tools,
running whichever ones the model asks for, and streaming the answer back.

**This module imports Streamlit, unlike its sibling ``chat.pins``.** ``_run_tool``
renders each tool's result inline into an ``st.expander`` so the user can see what
the model looked at, which binds the dispatch loop to the page. Passing a display
callback in would make the transport pure and is the obvious next cleanup; it was
left undone deliberately when this module was extracted, to keep that change a
mechanical move rather than a behavioural one.

The closed-set contract lives partly here: ``_ask_gemini`` is the only place tools
are dispatched, so any new tool must return rows drawn from the same context the
prompt was built from (see ``chat.tools``, which stays Streamlit-free and leaf for
that reason).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import cast

import pandas as pd
import streamlit as st
from common import reveal
from google import genai
from google.genai import types

from chat.prompt import ChatContext, build_system_message
from chat.tools import SHOWTIMES_TOOL, STREAMING_TOOL, TASTE_TOOL, showtimes_query, streaming_query, top_matches
from config import settings
from integrations.allocine import search_theaters

log = logging.getLogger(__name__)


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
    client = genai.Client(api_key=reveal(settings.gemini_api_key))
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
