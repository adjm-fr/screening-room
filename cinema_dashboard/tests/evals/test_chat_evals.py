"""LLM eval suite for the Recommendations chat.

Run with::

    uv run pytest tests/evals/ -m evals

The whole module is marked ``evals`` and deselected by the default
``-m 'not evals'`` in ``pyproject.toml``, so plain ``pytest tests/`` (incl.
CI) skips it. Requires ``GEMINI_API_KEY`` in the environment; without it the
suite skips at fixture setup.

There are two eval paths:

- ``test_chat_stays_in_bounds`` — the no-tool path, over ``GOLDENS``. Calls
  Gemini without tool declarations (mirrors ``_ask_gemini`` minus Streamlit +
  tools), so it verifies the *prompt* alone keeps the model inside the
  closed set.
- ``test_chat_tool_layer`` — the tool-enabled path, over ``TOOL_GOLDENS``.
  Passes the taste/showtime/streaming tool declarations and runs the same
  bounded round-trip loop ``chat.ui._ask_gemini`` does (minus Streamlit and
  minus ``search_theater``, which hits the live Allocine site and writes to
  ``theaters.csv`` — out of scope for a read-only eval), recording every
  call as a ``tools_called`` entry. This is the regression path for #49's
  narrow-the-block/recover-with-a-tool design: ``OVERCAP_STREAMING_GOLDEN``
  asks about a film dropped from the (capped) streaming block, which is only
  answerable if ``streaming_query`` actually fires.

The deterministic metrics (``FilmSetMembershipMetric``, ``StreamingClaimMetric``,
``ToolCorrectnessMetric``) always run. The LLM-as-judge metrics
(``FaithfulnessMetric``, ``AnswerRelevancyMetric``) are opt-in via ``--judge``
so we don't burn judge tokens on every push.
"""

from __future__ import annotations

from typing import cast

import pandas as pd
import pytest
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, ToolCorrectnessMetric
from deepeval.models import GeminiModel
from deepeval.test_case import LLMTestCase, ToolCall
from google import genai
from google.genai import types

from chat.prompt import ChatContext, build_system_message
from chat.tools import SHOWTIMES_TOOL, STREAMING_TOOL, TASTE_TOOL, showtimes_query, streaming_query, top_matches
from chat.ui import MAX_TOOL_ROUNDS
from config import settings
from tests.evals.goldens import GOLDENS, TOOL_GOLDENS, Golden
from tests.evals.metrics import FilmSetMembershipMetric, StreamingClaimMetric

# Bait titles a golden may try to lure the model into naming. Listed here so
# the metric can detect them in the output even when they don't appear in any
# allowed set. Keep this list aligned with the prompts in ``goldens.py``.
_BAIT_FILMS = [
    "Oppenheimer",
    "Parasite",
    "Interstellar",
    "Tenet",
    "Dune",
    "Barbie",
    # Bong Joon-ho filmography — bait for the director-style prompt.
    "Snowpiercer",
    "Memories of Murder",
    "The Host",
    "Mother",
    "Okja",
    "Mickey 17",
]

pytestmark = pytest.mark.evals


def _ctx_from_golden(g: Golden) -> ChatContext:
    return ChatContext(
        taste=g.taste,
        showtimes_md=g.showtimes_md,
        streaming_md=g.streaming_md,
        known_theaters=g.known_theaters,
        theaters_csv=None,
        wl_shows=pd.DataFrame(),
        # The no-tool eval path (test_chat_stays_in_bounds) never queries
        # these; the tool-enabled path (test_chat_tool_layer) queries
        # ``streaming_df`` through streaming_query, which is why goldens that
        # need one (OVERCAP_STREAMING_GOLDEN) carry it on the golden itself.
        wl_scored=pd.DataFrame(),
        streaming_df=g.streaming_df,
        # Pin re-linking is a UI concern; the evals only exercise the prompt.
        slug_by_title={},
        n_movies=len(g.allowed_films),
        n_screenings=0,
    )


def _ask_once(ctx: ChatContext, prompt: str) -> str:
    """Non-streaming, no-tool Gemini call. Mirrors `_ask_gemini` minus Streamlit + tools."""
    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=build_system_message(ctx)["content"],
            max_output_tokens=settings.gemini_max_tokens,
            temperature=settings.gemini_temperature,
            top_p=settings.gemini_top_p,
        ),
    )
    return resp.text or ""


def _dispatch_tool(ctx: ChatContext, name: str, args: dict) -> list[dict]:
    """Run one tool call against the golden's context, headless (no Streamlit UI).

    Mirrors ``chat.ui._run_tool``'s dispatch for the three pure, Streamlit-free
    tools (``chat.tools``); deliberately excludes ``search_theater`` — that one
    hits the live Allocine site and writes to ``theaters.csv``, which a
    read-only eval must not do. An unknown/unrouted name returns ``[]``, same
    fail-open contract the underlying handlers already guarantee.
    """
    if name == "top_matches":
        return top_matches(ctx.wl_scored, n=args.get("n") or 5, genre=args.get("genre"))
    if name == "showtimes_query":
        return showtimes_query(ctx.wl_scored, title=args.get("title"), theater=args.get("theater"), day=args.get("day"))
    if name == "streaming_query":
        return streaming_query(ctx.streaming_df, title=args.get("title"), provider=args.get("provider"))
    return []


def _ask_once_with_tools(ctx: ChatContext, prompt: str) -> tuple[str, list[ToolCall]]:
    """Non-streaming Gemini call with the taste/showtime/streaming tools enabled.

    Runs the same bounded round-trip loop ``chat.ui._ask_gemini`` does (up to
    ``MAX_TOOL_ROUNDS`` rounds, one function response per function call in a
    round), but non-streaming and dispatched through :func:`_dispatch_tool`
    instead of ``chat.ui._run_tool`` so no Streamlit runtime is required. Every
    call is recorded as a ``deepeval`` :class:`ToolCall` so ``ToolCorrectnessMetric``
    can assert on it.
    """
    client = genai.Client(api_key=settings.gemini_api_key)
    cfg = types.GenerateContentConfig(
        system_instruction=build_system_message(ctx)["content"],
        tools=[TASTE_TOOL, SHOWTIMES_TOOL, STREAMING_TOOL],
        max_output_tokens=settings.gemini_max_tokens,
        temperature=settings.gemini_temperature,
        top_p=settings.gemini_top_p,
    )
    convo: list[types.Content] = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    tools_called: list[ToolCall] = []
    output_text = ""

    # One extra iteration over the round budget: the last pass streams the
    # model's answer to the final tool result without granting a new call —
    # same shape as chat.ui._ask_gemini's loop.
    for round_index in range(MAX_TOOL_ROUNDS + 1):
        resp = client.models.generate_content(model=settings.gemini_model, contents=cast(list, convo), config=cfg)
        candidate = resp.candidates[0] if resp.candidates else None
        parts = list(candidate.content.parts or []) if candidate and candidate.content else []
        fn_calls = [p.function_call for p in parts if p.function_call]
        text = "".join(p.text for p in parts if p.text)
        if text:
            output_text = text

        if not fn_calls or round_index == MAX_TOOL_ROUNDS:
            break

        response_parts: list[types.Part] = []
        for fn_call in fn_calls:
            name = fn_call.name or ""
            args = dict(fn_call.args or {})
            rows = _dispatch_tool(ctx, name, args)
            tools_called.append(ToolCall(name=name, input_parameters=args, output=rows))
            response_parts.append(types.Part.from_function_response(name=name or "unknown", response={"results": rows}))

        convo = convo + [
            types.Content(role="model", parts=parts),
            types.Content(role="user", parts=response_parts),
        ]

    return output_text, tools_called


@pytest.fixture(scope="module")
def _require_gemini_key() -> None:
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not set — eval suite needs a live Gemini API key")


def _judge_model() -> GeminiModel:
    """The judge model backing every deepeval metric that needs one (ToolCorrectnessMetric's
    reason text, FaithfulnessMetric, AnswerRelevancyMetric). deepeval's built-in metrics default
    to an OpenAI judge (needs OPENAI_API_KEY, a key this project never asks for); pointing them at
    the same Gemini key/model the chat itself uses keeps the eval suite's key surface at one entry
    (GEMINI_API_KEY) instead of two.
    """
    return GeminiModel(model=settings.gemini_model, api_key=settings.gemini_api_key)


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda g: g.id)
def test_chat_stays_in_bounds(golden: Golden, _require_gemini_key: None) -> None:
    ctx = _ctx_from_golden(golden)
    output = _ask_once(ctx, golden.prompt)

    case = LLMTestCase(input=golden.prompt, actual_output=output)
    film_metric = FilmSetMembershipMetric(
        allowed_films=golden.allowed_films,
        candidate_outside_films=_BAIT_FILMS,
    )
    streaming_metric = StreamingClaimMetric(
        allowed_pairs=golden.allowed_streaming_pairs,
        allowed_films=golden.allowed_films,
    )

    film_metric.measure(case)
    streaming_metric.measure(case)

    failures = [m for m in (film_metric, streaming_metric) if not m.is_successful()]
    assert not failures, "\n".join(f"[{m.__name__}] {m.reason}\n---OUTPUT---\n{output}" for m in failures)


@pytest.mark.parametrize("golden", TOOL_GOLDENS, ids=lambda g: g.id)
def test_chat_tool_layer(golden: Golden, _require_gemini_key: None, judge_enabled: bool) -> None:
    """Tool-enabled eval path: asserts the model actually calls the tool it needs.

    ``ToolCorrectnessMetric`` is deterministic (no judge tokens) and the
    highest-priority assertion here — it directly regression-tests #49's
    narrow-the-block/recover-with-a-tool design via ``OVERCAP_STREAMING_GOLDEN``.
    ``StreamingClaimMetric`` is reused unchanged: the closed-set assertion
    holds the same whether the (film, provider) pair arrived via the prompt
    block or a tool result. ``FaithfulnessMetric``/``AnswerRelevancyMetric``
    are LLM-as-judge and only run with ``--judge``.
    """
    ctx = _ctx_from_golden(golden)
    output, tools_called = _ask_once_with_tools(ctx, golden.prompt)

    retrieval_context = [build_system_message(ctx)["content"], *(str(t.output) for t in tools_called)]
    case = LLMTestCase(
        input=golden.prompt,
        actual_output=output,
        tools_called=tools_called,
        expected_tools=[ToolCall(name=name) for name in sorted(golden.expected_tools)],
        retrieval_context=retrieval_context,
    )

    judge = _judge_model()
    # async_mode=False: deepeval's default async path calls asyncio.get_event_loop()
    # with no loop running under plain pytest, which raises under this project's
    # filterwarnings=["error"] (a DeprecationWarning promoted to a hard failure).
    tool_metric = ToolCorrectnessMetric(model=judge, async_mode=False)
    streaming_metric = StreamingClaimMetric(
        allowed_pairs=golden.allowed_streaming_pairs,
        allowed_films=golden.allowed_films,
    )
    metrics = [tool_metric, streaming_metric]

    if judge_enabled:
        # Same async_mode=False rationale as ToolCorrectnessMetric above.
        metrics += [
            FaithfulnessMetric(threshold=0.5, model=judge, async_mode=False),
            AnswerRelevancyMetric(threshold=0.5, model=judge, async_mode=False),
        ]

    for metric in metrics:
        metric.measure(case)

    failures = [m for m in metrics if not m.is_successful()]
    assert not failures, "\n".join(
        f"[{m.__name__}] {getattr(m, 'reason', None)}\n---OUTPUT---\n{output}\n---TOOLS CALLED---\n{tools_called}"
        for m in failures
    )
