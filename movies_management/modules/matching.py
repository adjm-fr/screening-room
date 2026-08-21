"""
Combined Allocine↔Letterboxd match scorer.

Pure — no I/O, no pandas, no network. :class:`Query` (the Allocine side) and
:class:`Candidate` (a cache row *or* a Letterboxd search hit) carry only the
already-cleaned fields the scorer needs; callers in ``allocine_enrichment``
are responsible for building them from a DataFrame row or a search result.

Replaces the two hand-tuned tiers that used to live in
``allocine_enrichment`` (exact-year + director-containment, then a
uniqueness-gated runtime-proximity fallback) with one continuous score. The
constants below (``WEIGHTS``, ``ACCEPT``, ``MARGIN``, ``YEAR_TAPER``,
``RUNTIME_TAPER``) are *meant* to be calibrated by
``modules/match_calibration.py`` against the real cache/showtimes parquets —
see that module for the ground-truth derivation and evaluation methodology.

**Calibrated 2026-08-22 against the real parquets** (6,788-row cache, the
328-film Allocine feed) via ``modules/match_calibration.py``. Measured, and
the numbers to regress against:

* **precision 1.0000, recall 0.9931** (288/290) on the harness's grouped
  ground truth — 0 wrong picks.
* **0 wrong picks in 132 adversarial probes**: every same-title *and*
  overlapping-director collision pair in the cache (22 of them, including the
  three genuinely-different-film pairs ``paranoia-1969``/
  ``a-quiet-place-to-kill``, ``wild-and-woolfy``/``little-red-walking-hood``
  and ``who-killed-who``/``thugs-with-dirty-mugs``), each probed in both
  directions and at year offsets 0/+1/-1. Result: 44 correct, 88 abstain,
  **0 wrong**. Abstaining on a genuine collision is the designed answer.
* End-to-end over the whole feed, cache path only (no network): 292 films
  resolved before, 296 after, **0 resolved to a different slug**. The six gains
  are cross-source drift the old exact matching could not cross —
  ``Andreï Zviaguintsev``/``Andrey Zvyagintsev`` (*Le Retour*), ``Loulou``/
  ``Pandora's Box``, a missing ``"Le"`` in the cache's *Cadet d'eau douce* — and
  the two losses are the duplicate rows below. On the search path 15 films that
  previously failed now resolve, including a correct ``None`` for one genuinely
  absent from Letterboxd.
* The two "false negatives" are **duplicate cache rows for one film**
  (``the-hero-of-friedrichstrasse-station``/``berlin-hero`` and
  ``jim-queen``/``jim-queen-and-the-quest-for-chloroqueer``): identical on
  every term, so ``MARGIN`` correctly refuses to choose. Effective recall on
  *distinct* films is 288/288.

**``MARGIN`` is the load-bearing constant, not ``ACCEPT``.** The joint sweep
(recall on positives x wrong-picks on the collision set) is flat in
``ACCEPT`` — every value from 0.60 to 0.80 yields the same 0.9931 recall —
but breaks in ``MARGIN``: at 0.03 or 0.05 the collision set produces 2-4
wrong picks at *every* ``ACCEPT``. Only ``MARGIN >= 0.08`` reaches zero, and
0.08 is safe solely at ``WEIGHTS["year"] == 0.20`` (raise the year weight to
0.30 and 0.08 breaks to 2 wrong). 0.12 is zero-wrong at both. So: do not
lower ``MARGIN`` to buy recall — there is no recall to buy, and it is the
only thing standing between this scorer and a silently wrong film.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

#: How many years apart two release years can sit before the year term goes to
#: zero. 3.0 gives 1.0 / 0.67 / 0.33 / 0 at deltas 0/1/2/3 — the observed
#: Allocine-vs-Letterboxd disagreement maxes out at 2 years across the feed.
YEAR_TAPER = 3.0

#: Below this title similarity the title term counts as *absent* rather than
#: contradictory. Two sources legitimately carry one film under wholly unrelated
#: names — ``Dans la ville blanche``/``In the White City``, ``Loulou``/
#: ``Pandora's Box``, ``Les Désaxés``/``The Misfits`` — so a low score is
#: absence of evidence, not evidence of mismatch, and letting it drag the total
#: down rejects correct cross-language matches (measured: the Tanner film scored
#: 0.66 against a 0.75 bar on an otherwise perfect director + adjacent year).
#: Above the floor the term still discriminates normally.
TITLE_EVIDENCE_FLOOR = 0.55

#: A runtime gap this large vetoes the match outright, whatever the other terms
#: say. The weighted mean alone cannot express this: runtime carries 0.09 of the
#: weight, so an exact title + exact director + adjacent year still scores ~0.82
#: with a 49-minute discrepancy — and same-title/same-director/adjacent-year is
#: precisely the theatrical-cut-vs-TV-cut shape (*Scenes from a Marriage*: a
#: 169-min film and a 281-min television version) the old runtime tier existed to
#: reject. Measured over the 289 genuine matches with a runtime on both sides:
#: p50 1.0, p90 4.0, p95 7.6, p99 16.4, **max 37.0** minutes, and zero above 45 —
#: so this vetoes no real match while keeping the guard the tiers used to provide.
RUNTIME_VETO_MINUTES = 45.0

#: How many minutes apart two runtimes can sit before the runtime term goes to
#: zero. 15.0 sits just above the p99 (14.8 min) of the runtime gap measured
#: over the films the old exact-year tier matched.
RUNTIME_TAPER = 15.0

#: Term weights for the weighted mean in :func:`score`. Renormalized over
#: whichever terms are actually available (``year``/``runtime`` may be
#: ``None``) — see :func:`score`'s docstring. Calibrated — see the module
#: docstring. Sums to 1.0 so the renormalisation is a no-op when every term is
#: available. The year weight is deliberately 0.27 rather than the 0.20 the
#: first draft used: it is what lets ``MARGIN`` stay at the robust 0.12.
WEIGHTS: dict[str, float] = {
    "title": 0.32,
    "director": 0.32,
    "year": 0.27,
    "runtime": 0.09,
}

#: Minimum total score for :func:`best_match` to accept a candidate at all.
#: The joint sweep is flat in this constant (0.60-0.80 all give recall
#: 0.9931), so it is set mid-range rather than tuned; ``MARGIN`` is what
#: actually guards precision. See the module docstring.
ACCEPT = 0.75

#: Minimum gap between the best and second-best candidate's total score for
#: :func:`best_match` to accept the best one. The continuous analogue of the
#: old tiers' "exactly one qualifying candidate, or nothing" uniqueness guard:
#: two candidates that are both plausible and close in score are exactly the
#: same "can't tell which one" situation that guard existed to catch.
#:
#: **This is the constant that carries precision — see the module docstring.**
#: Measured on the cache's 22 collision pairs: MARGIN 0.03/0.05 produce 2-4
#: wrong picks at every ACCEPT; 0.08 reaches zero but only while
#: ``WEIGHTS["year"]`` is 0.20; 0.12 is zero-wrong across both year weights
#: tried. The exact-year precedence question the design raised is settled
#: empirically rather than arithmetically: probing every collision pair at
#: year offsets 0/+1/-1 never once picked the wrong film — it abstains.
MARGIN = 0.12


@dataclass(frozen=True)
class Query:
    """The Allocine side of a match: one showtimes film tuple."""

    titles: tuple[str, ...]
    year: int | None
    director_tokens: tuple[frozenset[str], ...]
    runtime: float | None


@dataclass(frozen=True)
class Candidate:
    """The Letterboxd side of a match: a cache row, or a live search hit.

    ``runtime`` is ``None`` on search hits until the lazy-fetch tier in
    ``allocine_enrichment`` resolves it for a near-tie — see that module.
    """

    slug: str
    titles: tuple[str, ...]
    year: int | None
    director_tokens: tuple[frozenset[str], ...]
    runtime: float | None


@dataclass(frozen=True)
class Score:
    """A match score, broken down by term. ``total`` is what callers compare."""

    total: float
    title: float | None
    director: float
    year: float | None
    runtime: float | None


def _title_term(q_titles: tuple[str, ...], c_titles: tuple[str, ...]) -> float | None:
    """Max ``token_sort_ratio`` over every (query title, candidate title) pair.

    ``None`` when either side carries no usable title at all, so the term is
    renormalised away exactly like ``year``/``runtime`` are. Absent evidence is
    not the same as contradictory evidence: scoring a missing title as 0.0
    would let it veto an otherwise perfect director+year match. Every real
    cache row and every real Letterboxd search hit carries a title, so this
    only guards the degenerate case.

    ``token_sort_ratio`` (not a plain ratio) tolerates word-order differences
    between sources — e.g. subtitle punctuation or article placement — the
    same tolerance the old exact-normalised-title blocking step relied on,
    just continuous instead of binary.
    """
    best: float | None = None
    for qt in q_titles:
        if not qt:
            continue
        for ct in c_titles:
            if not ct:
                continue
            score = fuzz.token_sort_ratio(qt, ct) / 100.0
            best = score if best is None else max(best, score)
    if best is not None and best < TITLE_EVIDENCE_FLOOR:
        # See TITLE_EVIDENCE_FLOOR: too low to mean anything either way.
        return None
    return best


def _directors_overlap(a_tokens: Sequence[frozenset[str]], b_tokens: Sequence[frozenset[str]]) -> bool:
    """True when any director token set on one side contains (or is contained by) one on the other."""
    return any(a <= b or b <= a for a in a_tokens for b in b_tokens)


def _director_term(q_tokens: tuple[frozenset[str], ...], c_tokens: tuple[frozenset[str], ...]) -> float:
    """1.0 on containment (the existing rule), else the best fuzzy ratio.

    The containment shortcut is load-bearing: every director pair that
    matches under the old exact rule scores a full 1.0 here too, so fuzzy
    scoring can only add resolution *below* today's pass bar — it cannot
    regress a match that already works. Fuzzy is a fallback for the genuine
    transliteration/name-form gaps containment can't bridge (e.g.
    "Aleksandre Koberidze" vs "Alexandre Koberidze", a one-letter
    transliteration difference).
    """
    if not q_tokens or not c_tokens:
        return 0.0
    if _directors_overlap(q_tokens, c_tokens):
        return 1.0
    best = 0.0
    for qa in q_tokens:
        q_name = " ".join(sorted(qa))
        for cb in c_tokens:
            c_name = " ".join(sorted(cb))
            best = max(best, fuzz.token_set_ratio(q_name, c_name) / 100.0)
    return best


def _year_term(q_year: int | None, c_year: int | None) -> float | None:
    """Year proximity, or ``None`` when the *query* carries no year to compare.

    A candidate that has no year while the query does scores **0.0**, not
    ``None``. Renormalising it away would reward a record for carrying less
    data: on Letterboxd a year-less entry is a stub for an unreleased or
    unnamed film (``cosmos-1``, ``untitled-undertone-prequel``), and those were
    measured scoring 0.72-0.76 on title and director alone — close enough to
    the correct match to trip MARGIN and force an abstention. Absence on the
    candidate side is informative; absence on the query side is not.
    """
    if q_year is None:
        return None
    if c_year is None:
        return 0.0
    return max(0.0, 1.0 - abs(q_year - c_year) / YEAR_TAPER)


def _runtime_term(q_runtime: float | None, c_runtime: float | None) -> float | None:
    """Runtime proximity, or ``None`` when either side lacks one.

    Deliberately *not* the asymmetric rule :func:`_year_term` uses. A missing
    runtime on a candidate carries no signal: the Letterboxd search API returns
    ``slug``/``title``/``year``/``directors`` and never a runtime, so scoring
    its absence as 0.0 would tax every search hit identically rather than
    discriminate between them.
    """
    if q_runtime is None or c_runtime is None:
        return None
    return max(0.0, 1.0 - abs(q_runtime - c_runtime) / RUNTIME_TAPER)


def score(q: Query, c: Candidate, *, weights: dict[str, float] | None = None) -> Score:
    """Score ``c`` against ``q`` across title/director/year/runtime.

    ``total`` is a weighted mean over whichever terms are available: when
    ``year`` and/or ``runtime`` is ``None`` on either side, that term is
    dropped from both the numerator and the weight-sum denominator (rather
    than substituting a zero), so a candidate missing only a runtime is
    scored on title+director+year alone, not unfairly penalised for a field
    neither source always carries.

    ``weights`` defaults to the module-level :data:`WEIGHTS`; a caller may
    pass its own dict to sweep candidate weightings (see
    ``modules.match_calibration``) without mutating global state.
    """
    w = weights if weights is not None else WEIGHTS
    title = _title_term(q.titles, c.titles)
    director = _director_term(q.director_tokens, c.director_tokens)
    year = _year_term(q.year, c.year)
    runtime = _runtime_term(q.runtime, c.runtime)

    numerator = w["director"] * director
    denominator = w["director"]
    if title is not None:
        numerator += w["title"] * title
        denominator += w["title"]
    if year is not None:
        numerator += w["year"] * year
        denominator += w["year"]
    if runtime is not None:
        numerator += w["runtime"] * runtime
        denominator += w["runtime"]

    total = numerator / denominator if denominator else 0.0
    if q.runtime is not None and c.runtime is not None and abs(q.runtime - c.runtime) > RUNTIME_VETO_MINUTES:
        # See RUNTIME_VETO_MINUTES: a gross runtime mismatch is a different cut of
        # the film, not the film, and no amount of title/director agreement should
        # outvote it.
        total = 0.0
    return Score(total=total, title=title, director=director, year=year, runtime=runtime)


def best_match(
    q: Query,
    candidates: Sequence[Candidate],
    *,
    weights: dict[str, float] | None = None,
    accept: float | None = None,
    margin: float | None = None,
) -> tuple[Candidate, Score] | None:
    """The best-scoring candidate, or ``None`` if it doesn't clear ACCEPT/MARGIN.

    Ranks every candidate, then accepts the top one only when its score is
    at least ``accept`` (default :data:`ACCEPT`) *and* it leads the runner-up
    by at least ``margin`` (default :data:`MARGIN`). The margin check is the
    continuous analogue of the old tiers' "exactly one qualifying candidate,
    or nothing" uniqueness guard — two candidates that are both plausible and
    close in score is exactly the "can't tell which" situation that guard
    existed to catch; guessing would attach a wrong film's metadata silently
    and permanently.

    ``weights``/``accept``/``margin`` default to the module-level constants;
    a caller may override them to sweep candidate values (see
    ``modules.match_calibration``) without mutating global state.
    """
    if not candidates:
        return None
    accept_threshold = ACCEPT if accept is None else accept
    margin_threshold = MARGIN if margin is None else margin

    scored = sorted(((c, score(q, c, weights=weights)) for c in candidates), key=lambda pair: pair[1].total, reverse=True)
    top_candidate, top_score = scored[0]
    if top_score.total < accept_threshold:
        return None
    if len(scored) > 1:
        _, second_score = scored[1]
        if (top_score.total - second_score.total) < margin_threshold:
            return None
    return top_candidate, top_score
