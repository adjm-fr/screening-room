"""Golden dataset for the Recommendations chat LLM evals.

Each :class:`Golden` describes a prompt the chat could receive, the synthetic
`ChatContext` data the LLM will see, and the *allowed* film names and
(film, provider) pairs that may appear in the response. The metrics in
``tests/evals/metrics.py`` check that the LLM output stays inside those sets.

The goldens are deliberately small: a tight, well-curated set of bait prompts
is more useful than a sprawling one. Add a new golden whenever you find a new
failure mode in production.
"""

from __future__ import annotations

import dataclasses

# Films and providers used across goldens. Kept small so misses are obvious.
_WATCHLIST_FILMS = [
    "Perfect Days",
    "Past Lives",
    "Anatomy of a Fall",
    "The Zone of Interest",
    "Aftersun",
]
_STREAMING = {
    "Perfect Days": ["mubi"],
    "Past Lives": ["netflix"],
    "Aftersun": ["mubi", "arte"],
}
_THEATERS = ["MK2 Beaubourg", "Le Champo", "Reflet Médicis"]

_TASTE_PROFILE = (
    "Top genres: drama, art-house, slow cinema. "
    "Favorite directors: Wim Wenders, Jonathan Glazer, Celine Song. "
    "Average rating: 4.1/5."
)

_SHOWTIMES_MD = """\
| french_title         | letterboxd_title      | theater_name      | showtimes        | genres   |
|:---------------------|:----------------------|:------------------|:-----------------|:---------|
| Perfect Days         | Perfect Days          | MK2 Beaubourg     | 2026-05-28 20:00 | Drama    |
| Past Lives           | Past Lives            | Le Champo         | 2026-05-28 21:15 | Drama    |
| Anatomy d'une chute  | Anatomy of a Fall     | Reflet Médicis    | 2026-05-29 19:30 | Drama    |
| La Zone d'intérêt    | The Zone of Interest  | MK2 Beaubourg     | 2026-05-29 22:00 | Drama    |
| Aftersun             | Aftersun              | Le Champo         | 2026-05-30 18:00 | Drama    |
"""

_STREAMING_MD = "\n".join(f"- {title} — flatrate={', '.join(providers)}" for title, providers in _STREAMING.items())


@dataclasses.dataclass(frozen=True)
class Golden:
    """One eval case: a prompt + the bounds the model output must respect."""

    id: str
    prompt: str
    allowed_films: frozenset[str]
    allowed_streaming_pairs: frozenset[tuple[str, str]]  # (film, provider), both lowercased
    allowed_theaters: frozenset[str]

    @property
    def taste(self) -> str:
        return _TASTE_PROFILE

    @property
    def showtimes_md(self) -> str:
        return _SHOWTIMES_MD

    @property
    def streaming_md(self) -> str:
        return _STREAMING_MD

    @property
    def known_theaters(self) -> list[str]:
        return list(_THEATERS)


_ALLOWED_FILMS = frozenset(_WATCHLIST_FILMS)
_ALLOWED_PAIRS = frozenset((film.lower(), prov.lower()) for film, provs in _STREAMING.items() for prov in provs)
_ALLOWED_THEATERS = frozenset(_THEATERS)


GOLDENS: list[Golden] = [
    Golden(
        id="straight_tonight",
        prompt="What's playing tonight?",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="mubi_only",
        prompt="What can I watch on Mubi right now?",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="director_bait",
        prompt="Anything by Christopher Nolan tonight?",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="outside_film_bait",
        prompt="Recommend me Oppenheimer for tonight.",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="wrong_provider_bait",
        prompt="Is Parasite on Disney+?",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="weekend_pick",
        prompt="Pick something slow and contemplative for this weekend.",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="provider_for_film",
        prompt="Where can I stream Past Lives in France?",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
    Golden(
        id="similar_films_bait",
        prompt="Suggest films similar to Aftersun.",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
]
