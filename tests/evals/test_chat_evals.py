"""LLM eval suite for the Recommendations chat.

Run with::

    uv run pytest tests/evals/ -m evals

The whole module is marked ``evals`` and deselected by the default
``-m 'not evals'`` in ``pyproject.toml``, so plain ``pytest tests/`` (incl.
CI) skips it. Requires ``HF_API_KEY`` in the environment; without it the
suite skips at fixture setup.

The deterministic metrics (``FilmSetMembershipMetric``, ``StreamingClaimMetric``)
always run. The LLM-as-judge ``HallucinationMetric`` is opt-in via
``--judge`` so we don't burn judge tokens on every push.
"""

from __future__ import annotations

import pandas as pd
import pytest
from deepeval.test_case import LLMTestCase
from huggingface_hub import InferenceClient

from evals.goldens import GOLDENS, Golden
from evals.metrics import FilmSetMembershipMetric, StreamingClaimMetric
from modules.config import settings
from utils.chat import ChatContext, build_system_message

# Bait titles a golden may try to lure the model into naming. Listed here so
# the metric can detect them in the output even when they don't appear in any
# allowed set. Keep this list aligned with the prompts in ``goldens.py``.
_BAIT_FILMS = ["Oppenheimer", "Parasite", "Interstellar", "Tenet", "Dune", "Barbie"]

pytestmark = pytest.mark.evals


def _ctx_from_golden(g: Golden) -> ChatContext:
    return ChatContext(
        api_key=settings.hf_api_key or "",
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
    """Non-streaming, no-tool HF call. Mirrors `_ask_hf` minus Streamlit + tools."""
    client = InferenceClient(api_key=ctx.api_key)
    resp = client.chat.completions.create(
        model=settings.hf_model,
        messages=[build_system_message(ctx), {"role": "user", "content": prompt}],
        max_tokens=settings.hf_max_tokens,
        temperature=settings.hf_temperature,
        top_p=settings.hf_top_p,
    )
    return resp.choices[0].message.content or ""


@pytest.fixture(scope="module")
def _require_hf_key() -> None:
    if not settings.hf_api_key:
        pytest.skip("HF_API_KEY not set — eval suite needs a live HF Inference API key")


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda g: g.id)
def test_chat_stays_in_bounds(golden: Golden, _require_hf_key: None) -> None:
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
