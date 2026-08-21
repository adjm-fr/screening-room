"""Tests for modules/matching.py — the combined Allocine↔Letterboxd match scorer."""

from modules.allocine_enrichment import _split_director_tokens
from modules.matching import Candidate, Query, best_match, score


def _tokens(name: str) -> tuple[frozenset[str], ...]:
    return tuple(_split_director_tokens(name, "|"))


def _query(
    titles: tuple[str, ...] = ("Paprika",),
    year: int | None = 2005,
    director: str = "Satoshi Kon",
    runtime: float | None = 90.0,
) -> Query:
    return Query(titles=titles, year=year, director_tokens=_tokens(director), runtime=runtime)


def _candidate(
    slug: str = "paprika-2006",
    titles: tuple[str, ...] = ("Paprika",),
    year: int | None = 2006,
    director: str = "Satoshi Kon",
    runtime: float | None = 90.0,
) -> Candidate:
    return Candidate(slug=slug, titles=titles, year=year, director_tokens=_tokens(director), runtime=runtime)


# ── term isolation ──────────────────────────────────────────────────────────


def test_title_term_scores_identical_titles_at_one():
    s = score(_query(titles=("Paprika",)), _candidate(titles=("Paprika",)))
    assert s.title == 1.0


def test_title_term_tolerates_word_order():
    s = score(_query(titles=("The Misfits",)), _candidate(titles=("Misfits, The",)))
    assert s.title > 0.8


def test_title_term_is_absent_for_unrelated_titles():
    # Below TITLE_EVIDENCE_FLOOR the term reports None (absent), not a low float:
    # one film is routinely catalogued under wholly unrelated names in two
    # languages, so a low similarity is no evidence either way and must not drag
    # the total down. See matching.TITLE_EVIDENCE_FLOOR.
    s = score(_query(titles=("Paprika",)), _candidate(titles=("The Godfather",)))
    assert s.title is None


def test_title_term_still_discriminates_above_the_floor():
    close = score(_query(titles=("The Misfits",)), _candidate(titles=("The Misfits",)))
    apart = score(_query(titles=("The Misfits",)), _candidate(titles=("The Misfits 2021",)))
    assert close.title == 1.0
    assert apart.title is not None and apart.title < close.title


def test_a_candidate_without_a_year_is_penalised_not_excused():
    # A year-less Letterboxd record is a stub for an unreleased film. Renormalising
    # the term away would reward it for carrying less data and let it trip MARGIN
    # against the real match — measured on `cosmos-1`/`untitled-undertone-prequel`.
    stub = score(_query(year=2026), _candidate(year=None))
    assert stub.year == 0.0


def test_director_containment_shortcut_scores_one():
    # The existing containment rule from _directors_overlap: a suffix like
    # "Jr." on one side still scores a full 1.0, not a fuzzy approximation.
    s = score(_query(director="Akinola Davies Jr."), _candidate(director="Akinola Davies"))
    assert s.director == 1.0


def test_director_fuzzy_scores_transliteration_above_a_different_surname():
    # "Aleksandre Koberidze" vs "Alexandre Koberidze" is a genuine 1-edit
    # transliteration gap that containment cannot bridge — it must still
    # score clearly higher than a truly different director.
    close = score(_query(director="Aleksandre Koberidze"), _candidate(director="Alexandre Koberidze"))
    different = score(_query(director="Aleksandre Koberidze"), _candidate(director="Someone Else"))
    assert close.director > 0.85
    assert close.director > different.director


def test_director_term_zero_without_tokens_on_either_side():
    s = score(_query(director=""), _candidate(director="Satoshi Kon"))
    assert s.director == 0.0


def test_year_term_tapers_to_zero_over_three_years():
    same = score(_query(year=2005), _candidate(year=2005))
    one_off = score(_query(year=2005), _candidate(year=2006))
    two_off = score(_query(year=2005), _candidate(year=2007))
    three_off = score(_query(year=2005), _candidate(year=2008))
    assert same.year == 1.0
    assert 0.6 < one_off.year < 0.75
    assert 0.25 < two_off.year < 0.4
    assert three_off.year == 0.0


def test_year_term_none_when_either_side_missing():
    s = score(_query(year=None), _candidate(year=2005))
    assert s.year is None


def test_runtime_term_tapers_to_zero_over_fifteen_minutes():
    same = score(_query(runtime=90.0), _candidate(runtime=90.0))
    far = score(_query(runtime=90.0), _candidate(runtime=120.0))
    assert same.runtime == 1.0
    assert far.runtime == 0.0


def test_runtime_term_none_when_either_side_missing():
    s = score(_query(runtime=None), _candidate(runtime=90.0))
    assert s.runtime is None
    s2 = score(_query(runtime=90.0), _candidate(runtime=None))
    assert s2.runtime is None


# ── total: renormalization ──────────────────────────────────────────────────


def test_total_renormalizes_when_runtime_is_absent():
    # A candidate identical on title/director/year but missing runtime should
    # not be penalised relative to one that also matches on runtime — hold
    # year fixed (matched on both sides) so only the runtime term differs.
    with_runtime = score(_query(year=2005, runtime=90.0), _candidate(year=2005, runtime=90.0))
    without_runtime = score(_query(year=2005, runtime=90.0), _candidate(year=2005, runtime=None))
    assert with_runtime.total == without_runtime.total


def test_total_renormalizes_when_year_is_absent():
    # Hold runtime fixed (matched on both sides) so only the year term differs.
    with_year = score(_query(year=2005, runtime=90.0), _candidate(year=2005, runtime=90.0))
    without_year = score(_query(year=None, runtime=90.0), _candidate(year=2005, runtime=90.0))
    assert with_year.total == without_year.total


# ── best_match: accept/margin ───────────────────────────────────────────────


def test_best_match_accepts_a_clear_winner():
    q = _query()
    candidates = [_candidate(slug="paprika-2006", year=2006), _candidate(slug="unrelated", titles=("Nope",), year=1990)]
    result = best_match(q, candidates)
    assert result is not None
    winner, _ = result
    assert winner.slug == "paprika-2006"


def test_best_match_rejects_two_near_ties():
    q = _query()
    candidates = [
        _candidate(slug="candidate-a", year=2005),
        _candidate(slug="candidate-b", year=2005),
    ]
    assert best_match(q, candidates) is None


def test_best_match_rejects_below_accept_threshold():
    q = _query(titles=("Paprika",), director="Satoshi Kon", year=2005)
    candidates = [_candidate(slug="unrelated", titles=("Completely Different Film",), director="Nobody At All", year=1950)]
    assert best_match(q, candidates) is None


def test_best_match_none_on_empty_candidates():
    assert best_match(_query(), []) is None


def test_best_match_single_candidate_skips_margin_check():
    q = _query()
    result = best_match(q, [_candidate(year=2006)])
    assert result is not None


# ── the Jean-Luc regression (variant additivity) ────────────────────────────


def test_jean_luc_hyphen_variant_does_not_break_containment_in_the_scorer():
    s = score(_query(director="Jean-Luc Godard"), _candidate(director="Jean Luc Godard (II)"))
    assert s.director == 1.0
