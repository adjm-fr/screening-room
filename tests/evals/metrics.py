"""Deterministic DeepEval metrics for the Recommendations chat.

These metrics intentionally avoid an LLM judge:

- **FilmSetMembershipMetric** — every film name the model mentions must be in
  the allowed set (watchlist ∪ streaming-list). Catches "invented title"
  hallucinations.
- **StreamingClaimMetric** — every "<film> ... on <provider>" claim the model
  makes must appear in the allowed (film, provider) set. Catches "wrong
  streaming service" hallucinations.

Both run in milliseconds with no API costs and produce precise failure
messages. The richer (and slower) LLM-as-judge metrics are kept opt-in in
``test_chat_evals.py`` via a pytest flag.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

# Provider slugs the chat may legitimately reference. Anything outside is a
# made-up service. Source: TMDB FR flatrate providers we surface elsewhere.
_KNOWN_PROVIDERS = {
    "mubi",
    "netflix",
    "canalplus",
    "canal+",
    "arte",
    "amazon",
    "amazonprime",
    "amazon prime",
    "primevideo",
    "prime video",
    "disney+",
    "disneyplus",
    "appletv+",
    "apple tv+",
    "appletv",
    "ocs",
    "paramount+",
    "paramountplus",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", "", s.lower())


class FilmSetMembershipMetric(BaseMetric):
    """Fail if the output names a film outside the allowed set.

    The DeepEval ``LLMTestCase.context`` is expected to be a list whose first
    element is the pipe-separated, lowercased allowed film titles. We use that
    channel because DeepEval doesn't expose arbitrary metadata on the case.
    """

    threshold: float = 1.0
    strict_mode: bool = True
    async_mode: bool = False

    def __init__(self, allowed_films: Iterable[str], candidate_outside_films: Iterable[str] = ()):
        # ``candidate_outside_films`` lets a golden inject specific bait titles
        # (e.g. "Oppenheimer") that we explicitly scan for. Without this we'd
        # only catch films the metric already knows the name of.
        self.allowed_films = {f.lower() for f in allowed_films}
        self.candidate_outside_films = {f.lower() for f in candidate_outside_films}
        self.score: float = 0.0
        self.reason: str = ""
        self.success: bool = False
        self.error: str | None = None

    @property
    def __name__(self) -> str:  # DeepEval reads this for reports
        return "FilmSetMembership"

    def measure(self, test_case: LLMTestCase) -> float:
        out = (test_case.actual_output or "").lower()
        leaked = sorted(f for f in self.candidate_outside_films if f in out and f not in self.allowed_films)
        if leaked:
            self.score = 0.0
            self.success = False
            self.reason = f"output mentions film(s) outside the allowed set: {leaked}"
        else:
            self.score = 1.0
            self.success = True
            self.reason = "no out-of-set film names detected"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success


class StreamingClaimMetric(BaseMetric):
    """Fail if the output ties a film to a provider not in the allowed pairs.

    We look at every allowed film mentioned in the output, then scan a small
    window after the mention for known provider names. Any (film, provider)
    not in the allowed set is a violation.
    """

    threshold: float = 1.0
    strict_mode: bool = True
    async_mode: bool = False

    def __init__(self, allowed_pairs: Iterable[tuple[str, str]], allowed_films: Iterable[str]):
        self.allowed_pairs = {(f.lower(), _slug(p)) for f, p in allowed_pairs}
        self.allowed_films = list(allowed_films)
        self.score: float = 0.0
        self.reason: str = ""
        self.success: bool = False
        self.error: str | None = None

    @property
    def __name__(self) -> str:
        return "StreamingClaim"

    def measure(self, test_case: LLMTestCase) -> float:
        out = test_case.actual_output or ""
        out_lower = out.lower()
        violations: list[tuple[str, str]] = []

        for film in self.allowed_films:
            film_l = film.lower()
            for match in re.finditer(re.escape(film_l), out_lower):
                # Look at a 120-char window after the film mention for "on X"
                # or "streaming on X" claims.
                window = out_lower[match.end() : match.end() + 120]
                for provider in _KNOWN_PROVIDERS:
                    if re.search(rf"\b{re.escape(provider)}\b", window):
                        pair = (film_l, _slug(provider))
                        if pair not in self.allowed_pairs:
                            violations.append((film, provider))

        if violations:
            self.score = 0.0
            self.success = False
            self.reason = f"output makes unsupported streaming claim(s): {sorted(set(violations))}"
        else:
            self.score = 1.0
            self.success = True
            self.reason = "no unsupported streaming claims detected"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success
