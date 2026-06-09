"""LLM eval suite for the Recommendations chat.

Run with::

    uv run pytest tests/evals/ -m evals

The whole module is marked ``evals`` and deselected by the default
``-m 'not evals'`` in ``pyproject.toml``, so plain ``pytest tests/`` (incl.
CI) skips it. Requires ``GEMINI_API_KEY`` in the environment; without it the
suite skips at fixture setup.

The deterministic metrics (``FilmSetMembershipMetric``, ``StreamingClaimMetric``)
always run. The LLM-as-judge ``HallucinationMetric`` is opt-in via
``--judge`` so we don't burn judge tokens on every push.
"""

from __future__ import annotations

import pandas as pd
import pytest
from deepeval.test_case import LLMTestCase
from google import genai
from google.genai import types
from modules.config import settings
from utils.chat import ChatContext, build_system_message

from evals.goldens import GOLDENS, Golden
from evals.metrics import FilmSetMembershipMetric, StreamingClaimMetric

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


@pytest.fixture(scope="module")
def _require_gemini_key() -> None:
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not set — eval suite needs a live Gemini API key")


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
