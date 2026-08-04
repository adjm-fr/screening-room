"""Golden dataset for the Recommendations chat LLM evals.

Each :class:`Golden` describes a prompt the chat could receive, the synthetic
`ChatContext` data the LLM will see, and the *allowed* film names and
(film, provider) pairs that may appear in the response. The metrics in
``tests/evals/metrics.py`` check that the LLM output stays inside those sets.

The goldens are deliberately small: a tight, well-curated set of bait prompts
is more useful than a sprawling one. Add a new golden whenever you find a new
failure mode in production.

``TOOL_GOLDENS`` is a second, smaller list consumed only by the tool-enabled
eval path in ``test_chat_evals.py`` (``test_chat_tool_layer``) — goldens whose
point is specifically to exercise a tool, not just the no-tool prompt path
``GOLDENS`` covers. ``OVERCAP_STREAMING_GOLDEN`` is the regression case for
#49's narrow-the-block/recover-with-a-tool design: it builds
:func:`chat.prompt._streaming_context`'s real capped markdown block from a
streaming set larger than ``chat.prompt.STREAMING_CONTEXT_TOP_N``, then asks
about a film ranked below the cap — answerable only if ``streaming_query``
fires, since the film has no line in the injected block.
"""

from __future__ import annotations

import dataclasses

import pandas as pd

from chat.prompt import _streaming_context

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
    # Overrides for goldens that need a different streaming block/frame than
    # the shared ``_STREAMING`` fixture above — namely ``OVERCAP_STREAMING_GOLDEN``,
    # which needs a block big enough to trigger ``STREAMING_CONTEXT_TOP_N``
    # truncation. ``None``/empty means "use the shared fixture".
    streaming_md_override: str | None = None
    streaming_df: pd.DataFrame = dataclasses.field(default_factory=pd.DataFrame)
    # Tool names the tool-enabled eval path expects the model to call for this
    # golden (see ``test_chat_evals.py::test_chat_tool_layer``). Empty for
    # goldens that don't assert on tool usage.
    expected_tools: frozenset[str] = frozenset()

    @property
    def taste(self) -> str:
        return _TASTE_PROFILE

    @property
    def showtimes_md(self) -> str:
        return _SHOWTIMES_MD

    @property
    def streaming_md(self) -> str:
        return self.streaming_md_override if self.streaming_md_override is not None else _STREAMING_MD

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
    Golden(
        # Real prod failure (2026-05): model recommended "Snowpiercer on
        # Netflix" — neither in the watchlist nor in the streaming list.
        # "Director-style" prompts are especially baity because the model
        # has rich filmography knowledge it wants to surface.
        id="director_style_bait",
        prompt="Surprise me with a Bong Joon-ho-style movie",
        allowed_films=_ALLOWED_FILMS,
        allowed_streaming_pairs=_ALLOWED_PAIRS,
        allowed_theaters=_ALLOWED_THEATERS,
    ),
]


# --- Over-cap streaming golden (#50): the regression test for #49's design ---
#
# 60 films — comfortably over ``chat.prompt.STREAMING_CONTEXT_TOP_N`` (50) —
# each ranked by a descending synthetic ``match`` score, so film 01 is the
# top taste match and film 60 the lowest. Built as a real frame (not markdown
# by hand) and rendered through the production ``_streaming_context`` so the
# golden's block is byte-for-byte what the app would actually inject,
# including the trailing "call streaming_query" marker line.
_OVERCAP_STREAMING_FILMS: dict[str, list[str]] = {
    f"Overcap Film {i:02d}": ["mubi"] if i % 2 == 0 else ["netflix"] for i in range(1, 61)
}
# Ranked 55th by match — below the top-50 cap, so it gets no line in the
# rendered block and is answerable only through the streaming_query tool.
_OVERCAP_TARGET_FILM = "Overcap Film 55"
_OVERCAP_TARGET_PROVIDER = "netflix"

_OVERCAP_STREAMING_DF = pd.DataFrame(
    [
        {
            "letterboxd_title": title,
            "flatrate": providers,
            "free": [],
            "match": 1000 - i,  # descending: film 01 highest match, film 60 lowest
        }
        for i, (title, providers) in enumerate(_OVERCAP_STREAMING_FILMS.items(), start=1)
    ]
)
_OVERCAP_STREAMING_MD = _streaming_context(_OVERCAP_STREAMING_DF)
assert _OVERCAP_TARGET_FILM not in _OVERCAP_STREAMING_MD, (
    "fixture bug: the over-cap target film must NOT appear in the truncated block"
)

_OVERCAP_ALLOWED_FILMS = frozenset(_OVERCAP_STREAMING_FILMS)
_OVERCAP_ALLOWED_PAIRS = frozenset(
    (film.lower(), prov.lower()) for film, provs in _OVERCAP_STREAMING_FILMS.items() for prov in provs
)

OVERCAP_STREAMING_GOLDEN = Golden(
    id="overcap_streaming_below_cap",
    prompt=f"Is {_OVERCAP_TARGET_FILM} streaming anywhere?",
    allowed_films=_OVERCAP_ALLOWED_FILMS,
    allowed_streaming_pairs=_OVERCAP_ALLOWED_PAIRS,
    allowed_theaters=_ALLOWED_THEATERS,
    streaming_md_override=_OVERCAP_STREAMING_MD,
    streaming_df=_OVERCAP_STREAMING_DF,
    expected_tools=frozenset({"streaming_query"}),
)

# Goldens exercised only through the tool-enabled eval path
# (``test_chat_evals.py::test_chat_tool_layer``).
TOOL_GOLDENS: list[Golden] = [OVERCAP_STREAMING_GOLDEN]
