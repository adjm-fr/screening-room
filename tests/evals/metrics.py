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


# Phrases that, when they appear in a short window *before* a bait-film
# mention, indicate the model is refusing/declining to recommend the film
# rather than recommending it. The metric should not flag refusals.
_REFUSAL_PATTERNS = (
    r"can(?:no|')t (?:recommend|suggest|offer|propose|find|see|show|include)",
    r"cannot (?:recommend|suggest|offer|propose|find|see|show|include)",
    r"won'?t (?:recommend|suggest|find)",
    r"unable to (?:recommend|suggest|find|offer)",
    r"(?:is|are|it'?s) not (?:among|in|on|available|currently|playing|showing|listed)",
    r"isn'?t (?:among|in|on|available|currently|playing|showing|listed)",
    r"aren'?t (?:among|in|on|available|currently|playing|showing|listed)",
    r"no (?:showtimes?|screenings?|listings?) for",
    r"not (?:showing|playing|available|listed|in your|on your|among)",
    r"doesn'?t (?:appear|seem|show)",
    r"does not (?:appear|seem|show)",
)
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS))


def _is_in_refusal_context(text_lower: str, match_start: int, window: int = 120) -> bool:
    """True if a refusal phrase appears in the same sentence before ``match_start``.

    We bound the lookback at the nearest sentence terminator so a refusal in
    an earlier sentence ("I can't recommend X. But you should watch Y!")
    doesn't shield a recommendation in the next one.
    """
    pre = text_lower[max(0, match_start - window) : match_start]
    last_break = max(pre.rfind("."), pre.rfind("!"), pre.rfind("?"))
    if last_break != -1:
        pre = pre[last_break + 1 :]
    return bool(_REFUSAL_RE.search(pre))


# Negations that, when sitting between a film mention and a provider name,
# turn an "X on Y" co-occurrence into a denial rather than a claim.
_INLINE_NEGATION_RE = re.compile(r"\b(?:not|isn'?t|aren'?t|won'?t|wasn'?t|never|no longer|unavailable)\b")


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
        leaked: list[str] = []
        for film in sorted(self.candidate_outside_films):
            if film in self.allowed_films:
                continue
            # A bait film "leaks" only if it's mentioned outside a refusal
            # context. "I can't recommend Oppenheimer" is correct behavior;
            # "Watch Oppenheimer tonight" is the hallucination we care about.
            mentions = [m.start() for m in re.finditer(re.escape(film), out)]
            if mentions and any(not _is_in_refusal_context(out, pos) for pos in mentions):
                leaked.append(film)
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
        # Lowercased allowed-film names — used to truncate the post-mention
        # window so providers attributed to a *later* film in the same
        # sentence aren't falsely pinned on the current one.
        other_films_l = [f.lower() for f in self.allowed_films]

        for film in self.allowed_films:
            film_l = film.lower()
            for match in re.finditer(re.escape(film_l), out_lower):
                # Skip mentions inside a refusal — "X isn't on Netflix" is a
                # correct denial, not a wrong-provider claim.
                if _is_in_refusal_context(out_lower, match.start()):
                    continue
                # 120-char window after the film mention, truncated at the
                # next allowed-film mention so each film "owns" only the text
                # up to where the next film is introduced.
                window = out_lower[match.end() : match.end() + 120]
                next_positions = [window.find(o) for o in other_films_l if o != film_l]
                next_positions = [p for p in next_positions if p != -1]
                if next_positions:
                    window = window[: min(next_positions)]
                for provider in _KNOWN_PROVIDERS:
                    pm = re.search(rf"\b{re.escape(provider)}\b", window)
                    if not pm:
                        continue
                    # If there's an inline negation between the film and the
                    # provider ("Past Lives isn't on Netflix"), it's a denial,
                    # not a claim.
                    if _INLINE_NEGATION_RE.search(window[: pm.start()]):
                        continue
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
