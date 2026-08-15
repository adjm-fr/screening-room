"""
Movie data retrieval and caching from Letterboxd API.

This module handles fetching movie metadata from Letterboxd using the letterboxdpy library,
with efficient caching and parallel request processing.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import NamedTuple

import httpx
import pandas as pd
from letterboxdpy.movie import Movie
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from modules.tmdb import CreditMember, CreditsResponse, MovieBundle, MovieDetail, VideosResponse

logger = logging.getLogger(__name__)

TMDB_API_URL = "https://api.themoviedb.org/3"

# Shared retry policy for transient API failures: 3 attempts with exponential backoff,
# re-raising the final error so callers can degrade gracefully (return None / skip movie).
_RETRY_STOP = stop_after_attempt(3)
_RETRY_WAIT = wait_exponential(multiplier=1, max=10)


@retry(
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
async def _get_tmdb_movie(client: httpx.AsyncClient, tmdb_id: str, api_key: str) -> httpx.Response:
    """GET a TMDB movie in French, retrying on transport errors and 429/5xx responses.

    Deliberately carries no ``append_to_response``: ``language=fr-FR`` localises person
    names, so the credits must be fetched separately by ``_get_tmdb_bundle``. See
    ``modules.tmdb.MovieDetail``.
    """
    resp = await client.get(
        f"{TMDB_API_URL}/movie/{tmdb_id}",
        params={"language": "fr-FR", "api_key": api_key},
        timeout=10,
    )
    # Treat rate-limit / server errors as transient so tenacity retries them;
    # 4xx (other than 429) raise too but won't be retried (not in retry_if_exception_type
    # scope below) — caught by _fetch_french_title and surfaced as None.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


async def _fetch_french_title(client: httpx.AsyncClient, tmdb_id: str | None, api_key: str | None) -> str | None:
    """Fetch a film's French title from TMDB using an injected async client.

    Returns None when ``tmdb_id`` or ``api_key`` is falsy, on any non-200 response, when
    the payload fails validation (see ``modules.tmdb``), or when the request keeps
    failing after retries — never raises into the batch.
    """
    if not tmdb_id or not api_key:
        return None
    try:
        resp = await _get_tmdb_movie(client, tmdb_id, api_key)
        if resp.status_code == 200:
            return MovieDetail.model_validate(resp.json()).title
        logger.debug("TMDB returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except ValidationError as e:
        # Distinct from the generic handler below: a shape TMDB's payload no longer
        # matches is a schema-drift bug, not the normal "no French title" case, and
        # would otherwise be swallowed identically at debug level. See modules.tmdb.
        logger.warning("TMDB movie payload failed validation for tmdb_id=%s: %s", tmdb_id, e)
    except Exception as e:
        logger.debug("TMDB fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return None


@retry(
    stop=_RETRY_STOP,
    wait=_RETRY_WAIT,
    reraise=True,
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)
async def _get_tmdb_bundle(client: httpx.AsyncClient, tmdb_id: str, api_key: str) -> httpx.Response:
    """GET a movie's credits and videos in one request, retrying on transport errors and 429/5xx.

    ``append_to_response`` folds what used to be a ``/credits`` call and a ``/videos``
    call into this one, which TMDB bills as a single request. No ``language`` parameter:
    the credits must stay in their canonical (Latin) name forms — see
    ``modules.tmdb.MovieBundle``.
    """
    resp = await client.get(
        f"{TMDB_API_URL}/movie/{tmdb_id}",
        params={
            "append_to_response": "credits,videos",
            "include_video_language": "fr,en,null",
            "api_key": api_key,
        },
        timeout=10,
    )
    # Same contract as _get_tmdb_movie: 429/5xx are retried, other 4xx raise but aren't
    # retried — caught by _fetch_bundle and surfaced as an empty TmdbColumns.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    return resp


class Credits(NamedTuple):
    """The five cache columns TMDB's ``credits`` block fills.

    Field names match the cache column names exactly.
    """

    cast: str | None = None
    directors: str | None = None
    producers: str | None = None
    writers: str | None = None
    composers: str | None = None


class TmdbColumns(NamedTuple):
    """Every cache column one ``_get_tmdb_bundle`` request yields: credits, trailer, territories.

    Deliberately *not* in ``modules.tmdb`` beside ``MovieBundle``, which it is easy to
    mistake it for. That module models TMDB's **wire shapes** — its classes exist to
    validate incoming payloads and make schema drift raise instead of returning a bland
    ``None``. This is the other side of that boundary: the **parsed cache columns**, named
    after `data_letterboxd.parquet`'s own columns (via :class:`Credits`) and validating
    nothing. ``_fetch_bundle``'s job is exactly the crossing, ``MovieBundle`` ->
    ``TmdbColumns``, so keeping the two vocabularies in separate modules is what stops
    ``tmdb.py`` from having to know the cache's schema.

    ``credits`` stays nested because it mirrors a nested wire block; the rest are flat
    because they are flat fields of the same payload.
    """

    credits: Credits = Credits()
    trailer_url: str | None = None
    studio: str | None = None
    country: str | None = None
    origin_country: str | None = None
    language: str | None = None
    original_language: str | None = None


# TMDB returns `cast` pre-sorted by billing `order`, so the first entries are the leads —
# kept intentionally short (a full TMDB cast list runs to ~28 names) to keep the taste
# signal clean.
_CAST_BILLING_LIMIT = 8

# Crew job filters, one frozenset per cache column. Measured against the real cache
# (250-film sample), these reproduce the strings letterboxdpy used to supply on 98.4%
# (directors), 79.6% (writers) and 50.8% (producers) of films — and on 100% / 99.2% /
# 99.6% under the token-containment rule the showtimes join actually applies.
_DIRECTOR_JOBS = frozenset({"Director"})
# Deliberately just "Producer": TMDB also carries Executive/Co-/Associate Producer, which
# Letterboxd's `producer` list excludes. This is the closest semantic match, not the widest.
_PRODUCER_JOBS = frozenset({"Producer"})
# Letterboxd's flat `writer` list maps to these two TMDB jobs. The wider
# `department == "Writing"` filter also sweeps in Novel/Story/Characters credits, which
# Letterboxd keeps separate — it matched the cached strings on only 46% of the sample.
_WRITER_JOBS = frozenset({"Writer", "Screenplay"})
# The score's composer. Deliberately narrow: TMDB's older, looser `Music` job would lift
# coverage from 74% to 86% (and pre-1950 from 67% to 84%), but it also credits *source*
# music on films with no original score — `Ariel` comes back as six names ending in
# Tchaikovsky. Precision over reach, same call as `_PRODUCER_JOBS`. `Composer` is not a
# job string TMDB actually uses; adding it matched nothing. Multi-composer scores are real
# (Reznor/Ross, Carpenter/Lang) at ~6% of films, hence the comma-join.
_COMPOSER_JOBS = frozenset({"Original Music Composer"})


def _join_crew(crew: list[CreditMember], jobs: frozenset[str]) -> str | None:
    """Comma-join the names of every crew member holding one of ``jobs``, in API order.

    Deduplicated because TMDB lists a person once *per job*, so anyone credited both
    "Writer" and "Screenplay" would otherwise be named twice in ``writers``.
    """
    seen: set[str] = set()
    names: list[str] = []
    for member in crew:
        if member.job in jobs and member.name and member.name not in seen:
            seen.add(member.name)
            names.append(member.name)
    return ", ".join(names) or None


def _parse_credits(payload: CreditsResponse) -> Credits:
    """Split a validated ``credits`` block into the five cache columns it fills.

    Letterboxd's own crew is deliberately not read (see ``_fetch_movie``).
    """
    names = [member.name for member in payload.cast[:_CAST_BILLING_LIMIT] if member.name]
    return Credits(
        cast=", ".join(names) or None,
        directors=_join_crew(payload.crew, _DIRECTOR_JOBS),
        producers=_join_crew(payload.crew, _PRODUCER_JOBS),
        writers=_join_crew(payload.crew, _WRITER_JOBS),
        composers=_join_crew(payload.crew, _COMPOSER_JOBS),
    )


# Lower is better: French trailers are preferred over English, which are preferred over
# anything else. Anything not in this mapping (including missing iso_639_1) sorts last.
# Keyed on str | None (not just str) because Video.iso_639_1 is nullable.
_TRAILER_LANGUAGE_PRIORITY: dict[str | None, int] = {"fr": 0, "en": 1}


def _pick_trailer(payload: VideosResponse) -> str | None:
    """Pick the best official YouTube trailer out of a validated ``videos`` block.

    Filters to ``site == "YouTube"``, ``type == "Trailer"``, ``official is True``, then
    takes the best-language match: French, else English, else any other language (see
    ``_TRAILER_LANGUAGE_PRIORITY``). None when nothing matches.
    """
    trailers = [v for v in payload.results if v.site == "YouTube" and v.type == "Trailer" and v.official is True]
    if not trailers:
        return None
    best = min(trailers, key=lambda v: _TRAILER_LANGUAGE_PRIORITY.get(v.iso_639_1, 2))
    return f"https://www.youtube.com/watch?v={best.key}" if best.key else None


def _join_names(values: list[str | None]) -> str | None:
    """Comma-join a TMDB name list into one cache string, dropping blanks, preserving order.

    Deliberately *not* deduplicated, unlike ``_join_crew``: TMDB's territory lists carry a
    value once each, and the duplication the old Letterboxd ``language`` column suffered
    (Primary Language and Spoken Languages both linking under ``/films/language/``, so
    32.2% of rows repeated a value — *Frankenstein* came back
    ``"English, Danish, English, French"``) came from collapsing two distinct lists into
    one, which is exactly what reading ``spoken_languages`` alone stops doing.
    """
    return ", ".join(v for v in values if v) or None


def _parse_territories(payload: MovieBundle) -> TmdbColumns:
    """Read the five territory/provenance columns off a validated movie payload.

    Returns a :class:`TmdbColumns` carrying only those five, for the caller to merge with
    the credits and trailer parsed out of the same payload.

    ``country`` and ``origin_country`` are **different fields, not two spellings of one**
    — the confusion this whole column move started from. ``production_countries`` is the
    full co-production territory list (1.47 entries/film) and ``origin_country`` the
    nationality of the production (1.14). Measured on 400 films they are identical 73.7%
    of the time and never disjoint; where they differ ``origin`` is the subset in 96 of
    105 cases. The cache's historical Letterboxd ``country`` tracked *production* in all 9
    of the reverse cases, and the taste backtest agrees: swapping ``country`` to
    ``origin_country`` was the one variant that measurably lost (spearman 0.6668 vs
    0.6682). So ``country`` keeps the production list and ``origin_country`` is additive.

    Note the two are different vocabularies as well as different fields: TMDB ships
    ``origin_country`` as bare ISO 3166-1 codes with no display names anywhere in the
    payload, so it is stored as codes. Don't "fix" that into names with a lookup table
    without deciding what the ranker should key on.
    """
    return TmdbColumns(
        studio=_join_names([c.name for c in payload.production_companies]),
        country=_join_names([c.name for c in payload.production_countries]),
        origin_country=_join_names(list(payload.origin_country)),
        language=_join_names([lang.english_name for lang in payload.spoken_languages]),
        original_language=payload.original_language or None,
    )


async def _fetch_bundle(client: httpx.AsyncClient, tmdb_id: str | None, api_key: str | None) -> TmdbColumns:
    """Fetch a film's cast, crew, trailer and territories from TMDB in a single request.

    One ``append_to_response=credits,videos`` round-trip fills eleven cache columns — the
    five in :class:`Credits`, plus ``trailer_url``, plus the five
    :func:`_parse_territories` reads straight off the base movie payload.

    Returns an empty ``TmdbColumns`` when ``tmdb_id`` or ``api_key`` is falsy, on any
    non-200 response, when the payload fails validation (see ``modules.tmdb`` — this is
    the case that matters most: a 250-film sample of the real cache found a ``Director``
    credit on 100% of films, so a null ``directors`` from a malformed payload is far more
    likely a bug than a fact), or when the request keeps failing after retries — never
    raises into the batch. Note this makes ``directors`` null without a TMDB key, which
    the taste ranker and the watchlist↔showtimes join both depend on.
    """
    if not tmdb_id or not api_key:
        return TmdbColumns()
    try:
        resp = await _get_tmdb_bundle(client, tmdb_id, api_key)
        if resp.status_code == 200:
            payload = MovieBundle.model_validate(resp.json())
            return _parse_territories(payload)._replace(
                credits=_parse_credits(payload.credits),
                trailer_url=_pick_trailer(payload.videos),
            )
        logger.debug("TMDB bundle returned %d for tmdb_id=%s", resp.status_code, tmdb_id)
    except ValidationError as e:
        # See _fetch_french_title: a dedicated clause, before the generic handler below,
        # so a shape change surfaces at warning level instead of vanishing at debug.
        logger.warning("TMDB bundle payload failed validation for tmdb_id=%s: %s", tmdb_id, e)
    except Exception as e:
        logger.debug("TMDB bundle fetch failed for tmdb_id=%s: %s", tmdb_id, e)
    return TmdbColumns()


# The three Letterboxd detail types TMDB can replace, dropped from `**details_by_type` when
# `use_tmdb_territories` is on. Dropping them is load-bearing rather than tidy: the expansion
# is the *last* entry in _fetch_movie's dict literal, so a surviving Letterboxd "country"
# would overwrite the TMDB value seeded above it and the switch would be a silent no-op —
# right values fetched, wrong values written, no error anywhere. Letterboxd keeps serving all
# three types regardless of which producer this pipeline reads them from, which is exactly
# why the flag gates a *filter* rather than a fetch: both sources stay wired up, and flipping
# back is one boolean, not a revert.
_TMDB_OWNED_DETAIL_TYPES = frozenset({"studio", "country", "language"})


# Contract columns introduced after rows had already been cached, seeded (null) onto a
# loaded cache by `get_letterboxd_data`.
#
# This is a migration step, not a defensive guard, and the distinction is why it is here at
# all. Every other route by which a cache gains a column requires a row to actually be
# fetched: the concat below only fires when there is a *new* slug, and
# `refresh_letterboxd_data`'s pre-seed loop only when there is a *stale* one. Age is the
# only refresh trigger, so a cache that was fully rewritten by a recent backfill has neither
# — and would reach `write_parquet_validated` with a frame that predates these columns and
# fail the contract on every run, with `--reset_database` (refetch all ~6.7k films from
# Letterboxd, losing any whose page has since gone) as the only way out. That is not an
# escape hatch worth relying on.
#
# Values are NOT backfilled here: null means "not migrated yet" and the real values arrive
# with the one-pass backfill. Drop an entry once no cache in use predates it.
_SCHEMA_MIGRATION_COLUMNS = ("origin_country", "original_language")


@retry(stop=_RETRY_STOP, wait=_RETRY_WAIT, reraise=True)
def _build_movie(slug: str) -> Movie:
    """Construct a letterboxdpy ``Movie`` (the blocking scrape), retrying on transient errors."""
    return Movie(slug)


def _fetch_movie(slug: str, use_tmdb_territories: bool = False) -> dict | None:
    """
    Fetch movie metadata from Letterboxd for a single movie slug.

    Args:
        slug: The Letterboxd movie slug identifier.
        use_tmdb_territories: When True, drop Letterboxd's ``studio``/``country``/
            ``language`` detail types so ``_fetch_all`` can fill them from TMDB instead.
            When False (the default) they are read from Letterboxd exactly as before.

    Returns:
        Dictionary containing movie metadata (title, year, genres, ratings, etc.) with the
        TMDB-filled columns left as None — ``french_title``, ``cast``, the four crew
        columns and ``trailer_url``, plus the five territory columns (``studio``,
        ``country``, ``origin_country``, ``language``, ``original_language``). ``_fetch_all``
        fills them; under the default flag it fills only ``origin_country``/
        ``original_language``'s siblings and leaves the territory trio to Letterboxd below.
        Returns None if fetching fails.

    Note:
        - Letterboxd's own cast, crew, trailer, and popular_reviews fields are excluded
          from this output; ``cast`` (top-8 billed), the crew columns
          (``directors``/``producers``/``writers``/``composers``) and ``trailer_url`` are
          sourced from TMDB instead, mirroring how ``french_title`` is added — see
          ``_fetch_bundle``.
        - genres is split into genres/themes/mini_themes based on the "type" field.
        - **All five territory columns are keys of the returned dict in both flag
          positions**, so the parquet schema never depends on the flag. Under the default,
          ``studio``/``country``/``language`` carry Letterboxd's values and
          ``origin_country``/``original_language`` are the null placeholders that let the
          contract require them before the migration finishes. Any other detail type still
          expands into a key of its own.
    """
    try:
        movie = _build_movie(slug)

        # --- Genres / themes / mini-themes ---
        # movie.genres is a list[dict] with keys: type, name, slug, url
        # The "type" field comes from the Letterboxd URL path segment (genre, theme, mini-theme)
        raw_genres = movie.genres or []
        genres = ", ".join(g["name"] for g in raw_genres if g.get("type") == "genre") or None
        themes = ", ".join(g["name"] for g in raw_genres if g.get("type") == "theme") or None
        mini_themes = ", ".join(g["name"] for g in raw_genres if g.get("type") == "mini-theme") or None

        # --- Details (studio, country, language, … unless TMDB is serving those) ---
        # movie.details is a list[dict] with keys: type, name, slug, url
        # Group by type and comma-join names; each type becomes its own column.
        skip = _TMDB_OWNED_DETAIL_TYPES if use_tmdb_territories else frozenset()
        details_grouped: dict[str, list[str]] = {}
        for d in movie.details or []:
            t = d.get("type")
            if t and t not in skip:
                details_grouped.setdefault(t, []).append(d["name"])
        details_by_type = {t: ", ".join(names) for t, names in details_grouped.items()}

        return {
            # Identifiers
            "slug": slug,
            "movie_id": movie.id,
            "letterboxd_url": movie.url,
            "imdb_id": movie.imdb_id,
            "tmdb_id": movie.tmdb_id,
            "imdb_url": movie.imdb_link,
            "tmdb_url": movie.tmdb_link,
            # Core info
            "title": movie.title,
            "french_title": None,  # filled in by _fetch_all via TMDB
            "cast": None,  # filled in by _fetch_all via TMDB (top-8 billed)
            "trailer_url": None,  # filled in by _fetch_all via TMDB
            # Crew — filled in by _fetch_all via TMDB credits, see _fetch_bundle
            "directors": None,
            "producers": None,
            "writers": None,
            "composers": None,
            "original_title": movie.original_title,
            "release_year": movie.year,
            "runtime": movie.runtime,
            "tagline": movie.tagline,
            "description": movie.description,
            "letterboxd_avg_rating": movie.rating,
            # Media
            "poster_url": movie.poster,
            "banner_url": movie.banner,
            # Genres / themes
            "genres": genres,
            "themes": themes,
            "mini_themes": mini_themes,
            # Territories / provenance. Seeded unconditionally so the parquet carries all
            # five in both flag positions — the schema is fixed, only the values move.
            # Under the default, the expansion below overwrites the first three with
            # Letterboxd's values and the last two stay null (the placeholders the contract
            # requires); under `use_tmdb_territories`, the three are filtered out of the
            # expansion and `_fetch_all` fills all five from TMDB. `country` is then the
            # co-production list and `origin_country` the production's nationality as ISO
            # codes — different fields, not two spellings.
            "studio": None,
            "country": None,
            "origin_country": None,
            "language": None,
            "original_language": None,
            # Details — dynamic keys per type. Expanded LAST, so anything here wins over the
            # seeds above: that is the whole mechanism, and it is why switching producers is
            # a matter of filtering this dict rather than reordering it.
            **details_by_type,
        }
    except Exception as e:
        logger.error("Failed to fetch Movie data for slug '%s': %s", slug, e)
        return None


async def _fetch_all(
    slugs: list[str], api_key: str = "", concurrency: int = 20, *, use_tmdb_territories: bool = False
) -> list[dict | None]:
    """Run _fetch_movie for every slug concurrently, then attach TMDB enrichment.

    A single shared ``httpx.AsyncClient`` is opened for the whole batch so all TMDB
    lookups reuse pooled connections; the blocking Letterboxd scrape still runs in a
    worker thread per slug. The two TMDB lookups for a given movie run concurrently in a
    nested ``asyncio.TaskGroup``: ``_fetch_bundle`` fills eleven columns (cast, the four
    crew columns, trailer_url and the five territory columns) from one
    ``append_to_response`` request, and ``_fetch_french_title`` is the separate
    French-locale call that cannot be merged into it without localising the credits'
    person names (see ``modules.tmdb``).

    ``use_tmdb_territories`` gates only the *assignment* of those last five, not the fetch:
    they ride a payload already being requested for the credits, so parsing them
    unconditionally costs nothing and keeps the two code paths identical up to one branch.
    """
    sem = asyncio.Semaphore(concurrency)
    total = len(slugs)
    done = 0
    results: list[dict | None] = [None] * total

    async def _guarded(client: httpx.AsyncClient, i: int, slug: str) -> None:
        nonlocal done
        async with sem:
            result = await asyncio.to_thread(_fetch_movie, slug, use_tmdb_territories)
            if result is not None:
                tmdb_id = result.get("tmdb_id")
                async with asyncio.TaskGroup() as movie_tg:
                    french_title = movie_tg.create_task(_fetch_french_title(client, tmdb_id, api_key))
                    bundle = movie_tg.create_task(_fetch_bundle(client, tmdb_id, api_key))
                film_bundle = bundle.result()
                result["french_title"] = french_title.result()
                result["trailer_url"] = film_bundle.trailer_url
                # Assigned column-by-column rather than via _asdict() so each cache column
                # stays greppable from this module.
                film_credits = film_bundle.credits
                result["cast"] = film_credits.cast
                result["directors"] = film_credits.directors
                result["producers"] = film_credits.producers
                result["writers"] = film_credits.writers
                result["composers"] = film_credits.composers
                if use_tmdb_territories:
                    # Off by default: _fetch_movie has already put Letterboxd's values in
                    # studio/country/language, and overwriting them here is exactly the
                    # migration. origin_country/original_language have no Letterboxd
                    # equivalent, so they stay null until this flips — placeholders, not
                    # data, which is what lets the contract require them meanwhile.
                    result["studio"] = film_bundle.studio
                    result["country"] = film_bundle.country
                    result["origin_country"] = film_bundle.origin_country
                    result["language"] = film_bundle.language
                    result["original_language"] = film_bundle.original_language
            results[i] = result
        done += 1
        if done % 50 == 0 or done == total:
            logger.info("Fetched %d/%d", done, total)

    async with httpx.AsyncClient() as client, asyncio.TaskGroup() as tg:
        for i, slug in enumerate(slugs):
            tg.create_task(_guarded(client, i, slug))

    return results


def get_letterboxd_data(
    all_slugs: list[str], output_path: str | os.PathLike, api_key: str = "", *, use_tmdb_territories: bool = False
) -> pd.DataFrame:
    """
    Fetch Letterboxd movie data, reusing an on-disk cache to skip already-known slugs.

    Loads the existing cache from ``output_path`` (read-only) and fetches only
    new/missing movies using parallel requests. **Does not persist** — the caller
    owns the single cache write so it can assign provenance (``source``) first.

    Args:
        all_slugs: List of Letterboxd movie slugs to fetch data for.
        output_path: Path to the existing cache file, read to skip cached slugs.
        api_key: TMDB API key for authenticated requests.
        use_tmdb_territories: Source ``studio``/``country``/``language`` from TMDB rather
            than Letterboxd, and populate ``origin_country``/``original_language``. See
            ``Settings.use_tmdb_territories``; only affects rows fetched by this call.

    Returns:
        DataFrame combining the loaded cache and any newly fetched rows, with columns:
        slug, title, release_year, runtime, genres, description, tagline,
        letterboxd_avg_rating, directors, imdb_id, tmdb_id, letterboxd_url, imdb_url,
        tmdb_url, integration_date. The caller persists it (and assigns ``source``).
    """
    # Load existing cache to avoid refetching. Deliberately unvalidated: this is the
    # "no existing cache, start fresh" path (missing file, corrupt file, first run).
    # If schema validation raised here it would be swallowed by the except below and
    # silently rebuild the entire multi-thousand-film cache from scratch on what might
    # be a transient/partial read — a catastrophic, expensive, silent failure. The
    # single cache write each caller performs (write_parquet_validated against
    # DATA_LETTERBOXD) is what actually enforces the contract.
    try:
        data_df = pd.read_parquet(output_path)
        logger.info("Loaded existing cache: %d movies", data_df.shape[0])
    except Exception:
        logger.info("No existing cache found — starting fresh")
        data_df = pd.DataFrame()

    if not data_df.empty:
        added = [c for c in _SCHEMA_MIGRATION_COLUMNS if c not in data_df.columns]
        for col in added:
            data_df[col] = None
        if added:
            logger.info("Cache predates %s — seeded as null (values arrive on refresh)", ", ".join(added))

    # Identify slugs that need fetching
    cached_slugs = set(data_df["slug"].unique()) if not data_df.empty else set()
    new_slugs = [s for s in all_slugs if s not in cached_slugs]

    logger.info("New slugs to fetch: %d", len(new_slugs))

    if new_slugs:
        fetched = asyncio.run(_fetch_all(new_slugs, api_key, use_tmdb_territories=use_tmdb_territories))
        results = [r for r in fetched if r]  # Filter out None results from failed fetches

        if results:
            new_df = pd.DataFrame(results)
            # Mark when data was integrated into cache for refresh tracking
            now = pd.to_datetime(datetime.now().date())
            new_df["integration_date"] = now
            data_df = pd.concat([data_df, new_df], ignore_index=True)
            logger.info("Fetched %d new movies (caller persists)", len(results))
    else:
        logger.info("No new slugs to fetch")

    return data_df


def refresh_letterboxd_data(
    data_df: pd.DataFrame, slugs_to_refresh: list[str], api_key: str = "", *, use_tmdb_territories: bool = False
) -> pd.DataFrame:
    """
    Refetch metadata for the given slugs and return the updated DataFrame.

    Refetches movies that have aged beyond the configured days_to_update threshold,
    updating them in-place while preserving other entries (and their ``source``).
    **Does not persist** — the caller owns the single cache write.

    Args:
        data_df: Existing DataFrame with cached movie data.
        slugs_to_refresh: List of movie slugs to update.
        api_key: TMDB API key for authenticated requests.
        use_tmdb_territories: See :func:`get_letterboxd_data`. Refreshed rows are rewritten
            under the current setting, so flipping it mid-migration is what splits a taste
            affinity key in two — flip once, then backfill every row.

    Returns:
        Updated DataFrame with refreshed movie data and new integration_date.
        The caller persists it.
    """
    if not slugs_to_refresh:
        logger.info("No movies to refresh")
        return data_df

    logger.info("Refreshing %d movies", len(slugs_to_refresh))

    fetched = asyncio.run(_fetch_all(slugs_to_refresh, api_key, use_tmdb_territories=use_tmdb_territories))
    results = [r for r in fetched if r]

    fetched_slugs = {r["slug"] for r in results}
    dead_slugs = [s for s in slugs_to_refresh if s not in fetched_slugs]
    if dead_slugs:
        logger.info("Removing %d stale slug(s) no longer on Letterboxd: %s", len(dead_slugs), dead_slugs)
        data_df = data_df[~data_df["slug"].isin(dead_slugs)]

    if results:
        now = pd.to_datetime(datetime.now().date())
        refresh_df = pd.DataFrame(results)
        refresh_df["integration_date"] = now

        # DataFrame.update() silently ignores columns absent from the target, so refreshing
        # a cache built before a column existed (e.g. cast/trailer_url, added after earlier
        # rows were cached) would otherwise drop that column's refreshed values instead of
        # populating them. Pre-create any such columns (as null) so update() can fill them.
        for col in refresh_df.columns.difference(data_df.columns):
            data_df[col] = None

        # Update cache: merge refreshed data with existing, keyed by slug
        data_df = data_df.set_index("slug")
        data_df.update(refresh_df.set_index("slug"))
        data_df = data_df.reset_index()
        logger.info("Refreshed %d movies in cache", len(results))

    return data_df
