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

**These particular values are UNCALIBRATED.** The calibration run this
module was built for could not be executed: the sandboxed environment this
change was authored in has no read access to the user's real parquet files
(``~/Documents/...`` is blocked by macOS's per-process Documents-folder
permission, confirmed via three independent access paths — pandas, a direct
file read, and the calibration CLI's own ``click.Path(exists=True)`` check,
all raising the identical permission error). The values below are therefore
still the same reasoned-but-unmeasured placeholders commit 1 shipped, *not*
a result of the sweep in ``match_calibration.py --sweep``. Before relying on
this scorer for anything beyond the two tests that pin its qualitative
behaviour, run ``python match_calibration.py --showtimes-path ...`` (and
``--sweep``) from an environment that *can* read the real files, verify
100% precision on the negative (same-title-collision) set at or above
today's recall, and replace this whole comment with the measured numbers —
see ``match_calibration.py``'s module docstring for the full gate this is
supposed to pass before shipping commit 3 (rewiring ``allocine_enrichment``
onto this scorer). Commit 3 was deliberately not written because that gate
was never verified.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

#: How many years apart two release years can sit before the year term goes to
#: zero. UNCALIBRATED — see the module docstring.
YEAR_TAPER = 3.0

#: How many minutes apart two runtimes can sit before the runtime term goes to
#: zero. UNCALIBRATED — see the module docstring.
RUNTIME_TAPER = 15.0

#: Term weights for the weighted mean in :func:`score`. Renormalized over
#: whichever terms are actually available (``year``/``runtime`` may be
#: ``None``) — see :func:`score`'s docstring. UNCALIBRATED — see the module
#: docstring.
WEIGHTS: dict[str, float] = {
    "title": 0.35,
    "director": 0.35,
    "year": 0.2,
    "runtime": 0.1,
}

#: Minimum total score for :func:`best_match` to accept a candidate at all.
#: UNCALIBRATED — see the module docstring.
ACCEPT = 0.75

#: Minimum gap between the best and second-best candidate's total score for
#: :func:`best_match` to accept the best one. The continuous analogue of the
#: old tiers' "exactly one qualifying candidate, or nothing" uniqueness guard:
#: two candidates that are both plausible and close in score are exactly the
#: same "can't tell which one" situation that guard existed to catch.
#: UNCALIBRATED — see the module docstring. In particular, the exact-year
#: precedence question the design called out (whether an exact-year candidate
#: always outranks an otherwise-identical Δ1-year one, i.e. whether
#: ``WEIGHTS["year"] * (1 - 1/YEAR_TAPER) >= MARGIN``) has not been verified;
#: with the placeholder values above, ``0.2 * (1 - 1/3) ≈ 0.133 >= 0.08``
#: holds arithmetically, but that is not a substitute for the real sweep.
MARGIN = 0.08


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
    title: float
    director: float
    year: float | None
    runtime: float | None


def _title_term(q_titles: tuple[str, ...], c_titles: tuple[str, ...]) -> float:
    """Max ``token_sort_ratio`` over every (query title, candidate title) pair.

    ``token_sort_ratio`` (not a plain ratio) tolerates word-order differences
    between sources — e.g. subtitle punctuation or article placement — the
    same tolerance the old exact-normalised-title blocking step relied on,
    just continuous instead of binary.
    """
    best = 0.0
    for qt in q_titles:
        if not qt:
            continue
        for ct in c_titles:
            if not ct:
                continue
            best = max(best, fuzz.token_sort_ratio(qt, ct) / 100.0)
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
    if q_year is None or c_year is None:
        return None
    return max(0.0, 1.0 - abs(q_year - c_year) / YEAR_TAPER)


def _runtime_term(q_runtime: float | None, c_runtime: float | None) -> float | None:
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

    numerator = w["title"] * title + w["director"] * director
    denominator = w["title"] + w["director"]
    if year is not None:
        numerator += w["year"] * year
        denominator += w["year"]
    if runtime is not None:
        numerator += w["runtime"] * runtime
        denominator += w["runtime"]

    total = numerator / denominator if denominator else 0.0
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
