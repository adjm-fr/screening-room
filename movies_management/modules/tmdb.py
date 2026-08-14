"""
Pydantic models for the TMDB endpoints this module reads.

These models exist to draw a line between two failure modes that otherwise collapse
into one identical output — a bare-``.get()`` chain over raw JSON returns ``None`` both
when TMDB legitimately has no answer for a field (normal, expected — most films have no
French retitle) *and* when TMDB's response shape has changed underneath us (catastrophic
— it hits every film in the batch at once, and a `.get()` chain would silently swallow it
at ``debug`` level forever).

Validating through these models lets the fetchers in ``get_letterboxd_data`` tell the two
apart: a well-formed payload missing an *optional* field (e.g. no trailer, no French
title) still validates and yields ``None`` for that field, same as before this module
existed; a payload whose *shape* is wrong (a required field missing, a list where TMDB
used to send a list now sending something else) raises ``pydantic.ValidationError``,
which the fetchers catch in their own clause and log at ``logger.warning`` instead of
``logger.debug``.

Every model sets ``extra="ignore"``: TMDB's real payloads carry many more fields than any
of these models read (cast/crew alone run to a couple dozen keys per person), and strict
validation would reject every real response.

Two request shapes are modelled, and the split between them is load-bearing rather than
incidental — see ``MovieBundle`` and ``MovieDetail``.
"""

from typing import Annotated, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_T = TypeVar("_T")

# A list field that treats an explicit JSON `null` as an empty list.
#
# `default_factory` alone covers only an *absent* key, so a payload carrying
# `"production_companies": null` would raise ValidationError — and because the fetchers
# catch that for the whole payload, an *optional* field would take the *required* ones
# down with it: `_fetch_bundle` would return an empty `TmdbColumns()`, nulling `directors`
# and `cast` on a film whose credits parsed perfectly. It would also be logged as schema
# drift, which it is not. Absent, null and `[]` all mean the same thing here — "TMDB has
# nothing on record" — so they are normalised to one. Deliberately NOT applied to
# `MovieBundle.credits`/`videos`: a null there really is drift and must stay loud.
NullableList = Annotated[list[_T], BeforeValidator(lambda v: [] if v is None else v)]


class MovieDetail(BaseModel):
    """``GET /movie/{id}?language=fr-FR`` — only the field ``_fetch_french_title`` reads.

    This is the one request that must carry a locale, and therefore the one that cannot
    also carry the credits: ``language=fr-FR`` rewrites **person names** into the local
    script and name order (measured on 120 films: 5 director sets and 15 top-8 cast lists
    change, e.g. ``Ho Meng-Hua`` -> ``何夢華``, ``Marcell Jankovics`` ->
    ``Jankovics Marcell``). Those names feed the taste ranker's highest-weighted dimension
    and ``cinema_dashboard``'s token-containment director confirmation against Allocine,
    which is Latin-script — so localised names would silently drop films from the
    watchlist↔showtimes join. Everything else is locale-invariant (job strings, video
    results, countries, languages and company names were all identical across 60 films),
    which is why only ``title`` is read here and the rest comes from ``MovieBundle``.
    """

    model_config = ConfigDict(extra="ignore")

    title: str


class CreditMember(BaseModel):
    """One entry of ``GET /movie/{id}/credits``'s ``cast`` or ``crew`` list.

    ``job`` is only meaningful on crew entries and ``order`` only on cast entries; both
    default to ``None`` so a member from either list still validates without the other
    list's field.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    job: str | None = None
    order: int | None = None


class CreditsResponse(BaseModel):
    """``GET /movie/{id}/credits``."""

    model_config = ConfigDict(extra="ignore")

    cast: list[CreditMember] = Field(default_factory=list)
    crew: list[CreditMember] = Field(default_factory=list)


class Video(BaseModel):
    """One entry of ``GET /movie/{id}/videos``'s ``results`` list."""

    model_config = ConfigDict(extra="ignore")

    key: str | None = None
    site: str | None = None
    type: str | None = None
    official: bool | None = None
    iso_639_1: str | None = None


class VideosResponse(BaseModel):
    """``GET /movie/{id}/videos``, or the ``videos`` block of :class:`MovieBundle`."""

    model_config = ConfigDict(extra="ignore")

    results: list[Video] = Field(default_factory=list)


class Genre(BaseModel):
    """One entry of a movie payload's ``genres`` list — the ``genres`` column.

    ``name`` is **locale-sensitive**, which is why this rides :class:`MovieBundle` and not
    :class:`MovieDetail`: under ``language=fr-FR`` TMDB returns ``Drame``/``Science-Fiction``
    where the bare call returns ``Drama``/``Science Fiction``. The cache has always carried
    Letterboxd's English genre names and the taste ranker keys its affinities on them, so a
    localised value would split every genre into two affinity keys. Same hazard as the
    person names in :class:`MovieDetail`, different field.

    ``id`` is TMDB's stable genre id, carried because the vocabulary is a closed set of 19
    (unlike ``keywords``) and the id is the thing that survives a rename upstream.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: str | None = None


class Keyword(BaseModel):
    """One entry of the ``keywords`` block — a single free-form TMDB tag."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: str | None = None


class KeywordsResponse(BaseModel):
    """The ``keywords`` block of :class:`MovieBundle` — note the doubled nesting.

    ``append_to_response=keywords`` returns ``{"keywords": {"keywords": [...]}}``, i.e. an
    *object* wrapping the list, not the list itself (TV calls name the inner key
    ``results`` instead; only movies are fetched here). Modelling the wrapper is what makes
    the shape explicit rather than a surprise at the first ``[0]``.

    The inner list is a :data:`NullableList` even though the block is required: asking for
    the block guarantees the wrapper, not its contents, and a film with no tags on record
    is ordinary — TMDB's keyword vocabulary is crowd-maintained and thinner on older and
    non-English cinema.
    """

    model_config = ConfigDict(extra="ignore")

    keywords: NullableList[Keyword] = Field(default_factory=list)


class ProductionCompany(BaseModel):
    """One entry of a movie payload's ``production_companies`` list — the ``studio`` column."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class ProductionCountry(BaseModel):
    """One entry of ``production_countries`` — the ``country`` column.

    ``name`` is the display form ("United States of America"); ``iso_3166_1`` is the code
    the sibling ``origin_country`` list is made of, kept so the two are comparable.
    """

    model_config = ConfigDict(extra="ignore")

    iso_3166_1: str | None = None
    name: str | None = None


class SpokenLanguage(BaseModel):
    """One entry of ``spoken_languages`` — the ``language`` column.

    ``english_name`` is read rather than ``name``: ``name`` is the language's endonym
    (``Français``, ``日本語``), while ``english_name`` ("French", "Japanese") is the form
    the cache has always carried and the taste ranker keys its affinities on.
    """

    model_config = ConfigDict(extra="ignore")

    english_name: str | None = None
    iso_639_1: str | None = None
    name: str | None = None


class MovieBundle(BaseModel):
    """``GET /movie/{id}?append_to_response=credits,videos`` — no locale, see :class:`MovieDetail`.

    One request in place of the ``/credits`` and ``/videos`` calls it replaces, taking the
    per-film TMDB round-trips from three to two (``MovieDetail`` is the remaining one).
    TMDB counts an ``append_to_response`` bundle as a single request against the rate
    limit, and the appended blocks are byte-identical to the standalone endpoints: across
    150 films the new two-call shape reproduced the old three-call one exactly on
    french_title, directors, top-8 cast and trailer videos (0 mismatches each).

    ``credits``, ``videos`` and ``keywords`` are **required**, unlike every other field on
    these models: they are exactly the blocks this request asks for, so TMDB omitting one
    is the schema drift these models exist to surface, not a film that happens to have no
    data (an empty film still comes back as empty lists inside a present block). The limit
    is 20 appends, so further fields (``release_dates``, ``external_ids``, …) can still
    join this request at no extra cost.

    The territory/provenance fields below, and ``genres``, are **not** appended blocks —
    they are plain fields of the base movie payload this request already returns, so
    reading them costs nothing at all. They are optional, unlike the three requested
    blocks: the request does not ask for them by name, and TMDB legitimately has no
    company or country on record for obscure films — which is why those lists are
    :data:`NullableList`, so a field nobody asked for can never fail the payload that
    carries the credits. Their locale-invariance is measured, not assumed (60 films:
    ``production_countries``, ``spoken_languages`` and ``production_companies`` identical
    with and without ``language=fr-FR``), which is what allows them to ride this call
    rather than :class:`MovieDetail`'s.

    ``genres`` is the exception to that last sentence and the reason it must ride *this*
    call rather than the localised one: genre **names** are translated (``Drama`` ->
    ``Drame``), so fetching them alongside ``french_title`` would poison the taste
    ranker's second-heaviest dimension. See :class:`Genre`. ``keywords`` is locale-*in*
    variant in the other direction — the tags come back in English under ``fr-FR`` — but
    it is an appended block either way, so it rides here with the rest.
    """

    model_config = ConfigDict(extra="ignore")

    credits: CreditsResponse
    videos: VideosResponse
    keywords: KeywordsResponse
    genres: NullableList[Genre] = Field(default_factory=list)
    production_companies: NullableList[ProductionCompany] = Field(default_factory=list)
    production_countries: NullableList[ProductionCountry] = Field(default_factory=list)
    # Bare ISO 3166-1 codes, not objects — TMDB ships no display names for this one.
    origin_country: NullableList[str] = Field(default_factory=list)
    spoken_languages: NullableList[SpokenLanguage] = Field(default_factory=list)
    original_language: str | None = None
