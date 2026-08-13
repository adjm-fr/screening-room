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

from pydantic import BaseModel, ConfigDict, Field


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

    ``credits`` and ``videos`` are **required**, unlike every other field on these models:
    they are exactly the blocks this request asks for, so TMDB omitting one is the schema
    drift these models exist to surface, not a film that happens to have no data (an
    empty film still comes back as empty lists inside a present block). The limit is 20
    appends, so further fields (``keywords``, ``release_dates``, ``external_ids``, …)
    can join this request at no extra cost.

    The five territory/provenance fields below are **not** appended blocks — they are
    plain fields of the base movie payload this request already returns, so reading them
    costs nothing at all. They are optional, unlike ``credits``/``videos``: the request
    does not ask for them by name, and TMDB legitimately has no company or country on
    record for obscure films. Their locale-invariance is measured, not assumed (60 films:
    ``production_countries``, ``spoken_languages`` and ``production_companies`` identical
    with and without ``language=fr-FR``), which is what allows them to ride this call
    rather than :class:`MovieDetail`'s.
    """

    model_config = ConfigDict(extra="ignore")

    credits: CreditsResponse
    videos: VideosResponse
    production_companies: list[ProductionCompany] = Field(default_factory=list)
    production_countries: list[ProductionCountry] = Field(default_factory=list)
    # Bare ISO 3166-1 codes, not objects — TMDB ships no display names for this one.
    origin_country: list[str] = Field(default_factory=list)
    spoken_languages: list[SpokenLanguage] = Field(default_factory=list)
    original_language: str | None = None
