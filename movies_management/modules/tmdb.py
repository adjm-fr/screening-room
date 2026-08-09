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
validation would reject every real response. Deliberately scoped to only the three
endpoints in current use — a future consolidation onto `/movie/{id}?append_to_response=...`
is out of scope here.
"""

from pydantic import BaseModel, ConfigDict, Field


class MovieDetail(BaseModel):
    """``GET /movie/{id}`` — only the field ``_fetch_french_title`` reads."""

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
    """``GET /movie/{id}/videos``."""

    model_config = ConfigDict(extra="ignore")

    results: list[Video] = Field(default_factory=list)
