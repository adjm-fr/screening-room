"""
Allocine → Letterboxd cache enrichment.

Resolves Allocine film tuples (title, original_title, director, release_year,
runtime) to Letterboxd slugs and expands data_letterboxd.parquet to cover every
film in a showtimes parquet, not only the user's watchlist and ratings.
"""

import asyncio
import logging
import os
import re
import unicodedata

import pandas as pd
from common.parquet_io import read_parquet_validated, write_parquet_validated
from contracts import DATA_LETTERBOXD, SHOWTIMES
from letterboxdpy.search import Search, SearchFilter
from tenacity import retry, stop_after_attempt, wait_exponential

from modules.get_letterboxd_data import get_letterboxd_data

logger = logging.getLogger(__name__)

#: How far Allocine's runtime may sit from the cache's before the runtime-proximity
#: fallback (:func:`_match_by_runtime`) stops believing two rows are the same film.
#: Calibrated on the real parquets (Aug 2026) over the 308 films tier 1 matches
#: today: |Δ| is p50 1.0, p90 4.0, p95 8.0, p99 14.8, max 37.0 minutes, so ±10
#: covers 97.7% of genuine matches.
#:
#: The tolerance is *not* what makes the fallback safe — uniqueness is. A sweep of
#: the cache for same-title/overlapping-director pairs found 36, of which 30 sit
#: within this tolerance of each other, including the three that are genuinely
#: different films (``paranoia-1969``/``a-quiet-place-to-kill`` at 3 min,
#: ``wild-and-woolfy``/``little-red-walking-hood`` at 1, ``who-killed-who``/
#: ``thugs-with-dirty-mugs`` at 0). Tightening this number protects none of them;
#: refusing to choose between two qualifying candidates does.
RUNTIME_TOLERANCE_MINUTES = 10.0

#: Allocine's ``runtime`` display string, e.g. ``"2h 48min"`` (the only shape in the
#: real feed: all 107 distinct values match it, none null). Anchored end-to-end on
#: purpose — a string this doesn't fully account for parses to ``None`` and simply
#: skips tier 2, rather than silently yielding a wrong number of minutes (an
#: unanchored ``(\d+)\s*h`` would read ``"2h12"`` as 120).
_RUNTIME_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?\s*$", re.IGNORECASE)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def _search_films(query: str) -> list[dict]:
    """Run a Letterboxd film search, retrying on transient failures."""
    return Search(query, SearchFilter.FILMS).results.get("results", [])


#: Characters NFKD combining-strip cannot decompose (they aren't combining-mark
#: compositions, they're distinct letters with no canonical decomposition), plus
#: the two-letter Latin transliterations for the digraphs among them. Measured
#: against the real cache (Aug 2026): 12 rows carry ``ø``/``ł``/``Ø``/``ı``
#: (Rønning, Żuławski, Øvredal, Yılmaz Güney) that the accent strip alone leaves
#: untouched, which is what let ``Andrzej Zulawski`` vs ``Andrzej Żuławski`` keep
#: failing containment even after normalisation.
_EXTRA_LETTER_TRANSLATION = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "ı": "i",
        "İ": "I",
        "đ": "d",
        "Đ": "D",
        "ħ": "h",
        "ŧ": "t",
        "æ": "ae",
        "œ": "oe",
    }
)


def _director_tokens(name: str) -> list[frozenset[str]]:
    """Return the normalised name-token variants for a single director.

    NFKD normalises accents; the extra Latin-transliteration table above
    handles the letters NFKD can't (see :data:`_EXTRA_LETTER_TRANSLATION`);
    non-alpha chars become spaces (so hyphens, dotted initials, and
    parenthetical suffixes like ``"(II)"`` all split into tokens); everything
    is lower-cased. A token *set* (not a joined string) lets
    :func:`_directors_overlap` test containment, which tolerates the
    name-form drift between Allocine and Letterboxd that exact-string
    equality could not — e.g. Allocine's ``"S.S. Rajamouli"`` vs
    Letterboxd's ``"S. S. Rajamouli"``.

    Returns a **list of variant token sets**, not one set: when the name
    contains an apostrophe or hyphen, a second variant is added with that
    punctuation's tokens joined rather than split (``"Shin'ichirô Watanabe"``
    → both ``{shin, ichiro, watanabe}`` and ``{shinichiro, watanabe}``), so a
    match succeeds against either a source that splits on it
    (``"Park Sye-young"``) or one that doesn't (``"Syeyoung Park"``). The
    split variant is always kept *alongside* the joined one, never replaced —
    collapsing ``"Jean-Luc"`` to ``"jeanluc"`` outright would break
    ``"Jean-Luc Godard"`` vs ``"Jean Luc Godard (II)"``, whose split tokens
    are what makes one side a subset of the other; see
    ``test_director_tokens_keeps_the_split_variant_for_containment``.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.translate(_EXTRA_LETTER_TRANSLATION)
    split_form = "".join(c if c.isalpha() else " " for c in s).lower()
    variants = [frozenset(split_form.split())]

    if "'" in s or "-" in s:
        joined_form = "".join(c if c.isalpha() or c in "'-" else " " for c in s).lower()
        joined_form = joined_form.replace("'", "").replace("-", "")
        joined = frozenset(joined_form.split())
        if joined and joined not in variants:
            variants.append(joined)

    return [v for v in variants if v]


def _split_director_tokens(value: object, sep: str) -> list[frozenset[str]]:
    """Split a director string on ``sep`` into token-set variants, one group per director.

    ``isinstance`` (not just truthiness) guards against a NaN ``directors``
    cell from a real cache DataFrame — ``bool(float("nan"))`` is ``True``, so
    a plain ``if not value`` check lets it through to ``.split()`` and crashes.
    """
    if not isinstance(value, str) or not value:
        return []
    return [variant for name in value.split(sep) for variant in _director_tokens(name)]


def _letterboxd_result_director_tokens(item: dict) -> list[frozenset[str]]:
    """Extract token-set variants for every director in a Letterboxd search result."""
    return [variant for d in item.get("directors") or [] if (name := d.get("name")) for variant in _director_tokens(name)]


def _directors_overlap(a_tokens: list[frozenset[str]], b_tokens: list[frozenset[str]]) -> bool:
    """True when any director on one side is a token-subset (or superset) of one on the other.

    Containment, not equality, is what tolerates the name-form drift between
    sources (extra middle names, ``"Jr."``/``"(II)"`` suffixes, dotted-initial
    spacing) — see :func:`_director_tokens`.
    """
    return any(a <= b or b <= a for a in a_tokens for b in b_tokens)


def _search_letterboxd_slug(query: str, year_str: str | None, director: str | None) -> str | None:
    """Search Letterboxd for a film slug, scoring candidates by year and director match."""

    if not year_str or not director:
        logger.debug("Letterboxd search skipped: query=%r year=%s director=%s", query, year_str, director)
        return None

    logger.debug("Letterboxd search: query=%r year=%s director=%s", query, year_str, director)
    try:
        results = _search_films(query)
    except Exception as e:
        logger.debug("Letterboxd search failed for query=%r: %s", query, e)
        return None

    logger.debug("  → %d candidates for query=%r", len(results), query)
    allocine_tokens = _split_director_tokens(director, "|")

    for item in results:
        item_year = str(item.get("year", ""))
        if item_year != year_str:
            logger.debug("    skip slug=%s: year %s ≠ %s", item.get("slug"), item_year, year_str)
            continue
        lb_tokens = _letterboxd_result_director_tokens(item)
        if _directors_overlap(allocine_tokens, lb_tokens):
            slug = item.get("slug") or None
            logger.debug("    match slug=%s (directors %s)", slug, allocine_tokens)
            return slug
        logger.debug("    skip slug=%s: no director overlap (%s vs %s)", item.get("slug"), allocine_tokens, lb_tokens)

    return None


async def resolve_slug_from_allocine_tuple(
    title: str,
    original_title: str | None,
    director: str | None,
    release_year: int | str | None,
) -> str | None:
    """Resolve a Letterboxd slug from an Allocine film tuple.

    Strategy:
    1. Search Letterboxd by ``title``, post-filter candidates by year and director.
    2. Fall back to ``original_title`` if the first search yields nothing.

    Films that can't be resolved against Letterboxd are dropped from downstream
    processing — there is no TMDB fallback.

    The blocking Letterboxd ``Search`` call is offloaded to a worker thread via
    ``asyncio.to_thread`` so a batch of resolutions can run concurrently from
    a single event loop.

    Args:
        title: French display title from Allocine.
        original_title: Original-language title (may be None or identical to title).
        director: Director name string from Allocine (used for post-filtering).
        release_year: 4-digit release year (int or str).  May be None.

    Returns:
        A Letterboxd slug string, or ``None`` if resolution failed.
    """
    try:
        year_str = str(int(release_year)) if release_year else None
    except (TypeError, ValueError):
        year_str = str(release_year) if release_year else None

    slug = await asyncio.to_thread(_search_letterboxd_slug, title, year_str, director)
    if not slug and original_title and original_title != title:
        logger.debug("title miss for %r, trying original_title=%r", title, original_title)
        slug = await asyncio.to_thread(_search_letterboxd_slug, original_title, year_str, director)

    logger.debug("resolved %r → %s", title, slug)
    return slug


async def _resolve_all_slugs(films: list[dict], concurrency: int = 10) -> list[str | None]:
    """Resolve a batch of Allocine film tuples to Letterboxd slugs concurrently.

    Mirrors the ``_fetch_all`` shape in ``get_letterboxd_data``: a semaphore caps
    in-flight searches, a TaskGroup awaits all tasks, and progress is logged every
    50 completions.
    """
    sem = asyncio.Semaphore(concurrency)
    total = len(films)
    done = 0
    results: list[str | None] = [None] * total

    async def _guarded(i: int, film: dict) -> None:
        nonlocal done
        async with sem:
            results[i] = await resolve_slug_from_allocine_tuple(
                film["title"], film["original_title"], film["director"], film["release_year"]
            )
        done += 1
        if done % 50 == 0 or done == total:
            logger.info("Resolved %d/%d", done, total)

    async with asyncio.TaskGroup() as tg:
        for i, film in enumerate(films):
            tg.create_task(_guarded(i, film))

    return results


def _normalize_title(raw: object) -> str:
    """Return a canonical form of a title for cache join-key matching.

    Strips accents, lowercases, replaces non-alphanumeric chars with spaces,
    then collapses whitespace. Returns an empty string for null/non-str input.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c if c.isalnum() else " " for c in s).lower()
    return " ".join(s.split())


def _parse_runtime(raw: object) -> float | None:
    """Minutes from Allocine's raw runtime string (``"2h 48min"``), or ``None``.

    ``contracts.SHOWTIMES`` documents ``runtime`` as a raw display string, not
    parsed minutes, and allows it to be null. Every non-string, empty or
    unrecognised value therefore returns ``None`` rather than raising — the
    callers treat that as "no runtime evidence", which just skips the fallback
    tier and leaves the film unresolved exactly as it is today.
    """
    if not isinstance(raw, str):
        return None
    match = _RUNTIME_RE.match(raw)
    if not match:
        return None
    hours, minutes = match.group(1), match.group(2)
    if hours is None and minutes is None:
        return None
    return float((int(hours) * 60 if hours else 0) + (int(minutes) if minutes else 0))


def _build_cache_index(cache_df: pd.DataFrame) -> dict[str, list[dict]]:
    """Index the existing cache by normalised title for cache-first matching.

    Maps each normalised ``title``/``french_title``/``original_title`` to the
    cache rows that carry it, so a showtimes film can be matched directly
    against ``data_letterboxd.parquet`` before ever hitting Letterboxd — a
    film already in the cache under a slightly different director spelling
    (see :func:`_director_tokens`) would otherwise be re-searched and dropped
    as unresolved on every run.
    """
    index: dict[str, list[dict]] = {}
    for row in cache_df.itertuples():
        slug = getattr(row, "slug", None)
        if not slug or pd.isna(slug):
            continue
        raw_year = getattr(row, "release_year", None)
        try:
            release_year = int(raw_year) if raw_year is not None else None
        except (TypeError, ValueError):
            release_year = None
        # Cache `runtime` is float64 minutes; a NaN cell must become None, not nan —
        # every arithmetic comparison against nan is False, which would read as
        # "out of tolerance" rather than "no runtime to compare".
        raw_runtime = getattr(row, "runtime", None)
        try:
            runtime = None if raw_runtime is None or pd.isna(raw_runtime) else float(raw_runtime)
        except (TypeError, ValueError):
            runtime = None
        entry = {
            "slug": slug,
            "release_year": release_year,
            "runtime": runtime,
            "director_tokens": _split_director_tokens(getattr(row, "directors", None), ", "),
        }
        for col in ("title", "french_title", "original_title"):
            norm = _normalize_title(getattr(row, col, None))
            if norm:
                index.setdefault(norm, []).append(entry)
    return index


def _confirmed_candidates(film: dict, index: dict[str, list[dict]], allocine_tokens: list[frozenset[str]]) -> list[dict]:
    """Cache entries sharing a normalised title with ``film`` and a confirmed director.

    Deduped by slug and ordered ``title`` before ``original_title``, because
    :func:`_build_cache_index` files one entry under every title spelling the row
    carries: a cache row whose ``title`` and ``original_title`` both normalise to
    the Allocine title would otherwise be counted twice, and
    :func:`_match_by_runtime` would refuse it as "ambiguous" against itself.
    """
    confirmed: dict[str, dict] = {}
    for title in (film["title"], film["original_title"]):
        norm = _normalize_title(title)
        if not norm:
            continue
        for candidate in index.get(norm, []):
            if candidate["slug"] in confirmed:
                continue
            if _directors_overlap(allocine_tokens, candidate["director_tokens"]):
                confirmed[candidate["slug"]] = candidate
    return list(confirmed.values())


def _match_by_runtime(film: dict, candidates: list[dict]) -> str | None:
    """Pick the single candidate whose runtime is within tolerance of Allocine's.

    The fallback tier, reached only when no candidate's ``release_year`` matched
    exactly. Allocine sometimes carries a film's *production* year where
    Letterboxd carries its *release* year (Bergman's *Scenes from a Marriage*:
    Allocine 1973, cache 1974), and the resulting miss is worse than a bare
    non-match — the film falls through to :func:`_search_letterboxd_slug`, whose
    identical hard year filter then resolves the *other* Letterboxd entry for the
    same film (``scenes-from-a-marriage-1973-1``, the 281-minute TV cut, which
    TMDB catalogues under ``/tv/`` and so carries no ``tmdb_id`` and no TMDB
    columns at all).

    **Exactly one qualifying candidate, or nothing.** Two or more means the title
    and director alone don't identify the film and only the year ever did, so
    guessing would attach a wrong film's metadata silently and permanently — the
    same call ``chat.pins.resolve_pin`` makes in ``cinema_dashboard``, where an
    unlinked pin beats a wrong one. See :data:`RUNTIME_TOLERANCE_MINUTES` for why
    the guard is uniqueness rather than a tighter tolerance.
    """
    allocine_runtime = _parse_runtime(film.get("runtime"))
    if allocine_runtime is None:
        return None

    close = [
        candidate
        for candidate in candidates
        if candidate["runtime"] is not None and abs(candidate["runtime"] - allocine_runtime) <= RUNTIME_TOLERANCE_MINUTES
    ]
    if len(close) != 1:
        if close:
            logger.debug(
                "    runtime fallback declined for %r: %d candidates within %g min (%s)",
                film["title"],
                len(close),
                RUNTIME_TOLERANCE_MINUTES,
                [c["slug"] for c in close],
            )
        return None

    match = close[0]
    logger.debug(
        "    runtime fallback matched %r → %s (cache year %s ≠ allocine %s, Δruntime %g min)",
        film["title"],
        match["slug"],
        match["release_year"],
        film["release_year"],
        abs(match["runtime"] - allocine_runtime),
    )
    return match["slug"]


def _match_cache(film: dict, index: dict[str, list[dict]]) -> str | None:
    """Resolve a film directly against the cache index, confirmed by director overlap.

    Two tiers, both requiring a normalised title hit and director-token overlap:

    1. **Exact ``release_year``** — handles ~99% of traffic (308 of 351 films in
       the real feed) and is what keeps every same-title/overlapping-director
       collision in the cache separated.
    2. **Runtime proximity** (:func:`_match_by_runtime`), only when tier 1 found
       nothing and only when exactly one candidate qualifies.

    The order matters and is not interchangeable: runtime cannot separate 30 of
    the 36 colliding pairs in the cache, so it must never be consulted while a
    year still can.

    Rejects a match with no parseable ``release_year`` or no director tokens on
    either side — the same precision-first rule :func:`_search_letterboxd_slug`
    applies to a live search, needed here too so a recurring French title
    (e.g. ``"Le Retour"``, two unrelated films from 2003 and 2023) can't
    attach to the wrong cache row. Note that a *missing* year still rejects
    outright rather than falling through to tier 2: the fallback exists for the
    measured case where the two sources disagree about a year, not for films that
    carry none, which nothing here has ever matched.
    """
    try:
        year = int(film["release_year"])
    except (TypeError, ValueError):
        return None
    allocine_tokens = _split_director_tokens(film["director"], "|")
    if not allocine_tokens:
        return None

    candidates = _confirmed_candidates(film, index, allocine_tokens)
    for candidate in candidates:
        if candidate["release_year"] == year:
            return candidate["slug"]
    return _match_by_runtime(film, candidates)


def enrich_cache_from_showtimes(
    showtimes_path: str | os.PathLike,
    cache_path: str | os.PathLike,
    unresolved_path: str | os.PathLike,
    api_key: str = "",
) -> None:
    """Expand the Letterboxd metadata cache with films found in a showtimes parquet.

    Reads unique (movie, original_title, director, release_year, runtime) tuples
    from ``showtimes_path``. Each tuple is first matched directly against
    ``cache_path`` (title + release_year, confirmed by director token overlap,
    falling back to a uniquely-close runtime when the two sources disagree about
    the year — see :func:`_match_cache`); only films that don't already have a
    cache row are resolved via a live Letterboxd search. New slugs get full
    metadata via ``get_letterboxd_data``, stamped with
    ``source="allocine_showtimes"``, and persisted to ``cache_path``; tuples
    that still can't be resolved are written to ``unresolved_path`` for
    visibility.

    Args:
        showtimes_path: Path to the Allocine showtimes parquet.
        cache_path: Path to ``data_letterboxd.parquet`` (read + written in-place).
        unresolved_path: Destination for films that could not be resolved.
        api_key: TMDB API key; forwarded to ``get_letterboxd_data`` to fetch French titles.
    """
    logger.info("Enriching Letterboxd cache from showtimes: %s", showtimes_path)
    showtimes_df = read_parquet_validated(showtimes_path, required_columns=SHOWTIMES.required_columns, label="showtimes")

    # One row per distinct film — not per showtime slot. `runtime` rides along for
    # _match_cache's fallback tier without changing that grain: it is a property of
    # the film, not the screening, so the real feed yields the same 351 unique films
    # with or without it. The unresolved parquet below still writes only the 4-tuple
    # its consumer (cinema_dashboard's build_unresolved_showtimes) joins back on.
    key_cols = [c for c in ("movie", "original_title", "director", "release_year", "runtime") if c in showtimes_df.columns]
    unique_films = showtimes_df[key_cols].drop_duplicates().reset_index(drop=True)
    logger.info("Unique films in showtimes: %d", len(unique_films))

    try:
        # Deliberately unvalidated: this is the "no existing cache, start fresh" path.
        # If schema validation raised here it would be swallowed by the except below and
        # silently rebuild the entire 6,751-film cache from scratch on a transient/partial
        # read — a catastrophic, expensive, silent failure. The cache write further down
        # (write_parquet_validated) is what actually enforces the DATA_LETTERBOXD contract.
        cache_df = pd.read_parquet(cache_path)
        cached_slugs: set[str] = set(cache_df["slug"].dropna().unique())
    except Exception:
        cache_df = pd.DataFrame()
        cached_slugs = set()
    cache_index = _build_cache_index(cache_df)
    logger.debug("cached_slugs: %d preloaded", len(cached_slugs))

    # Lift per-row cleanup out of the loop so the resolver gets a clean list[dict]
    films: list[dict] = []
    for _, row in unique_films.iterrows():
        title = str(row.get("movie") or "").strip()
        if not title:
            continue
        films.append(
            {
                "title": title,
                "original_title": str(row.get("original_title") or "").strip() or None,
                "director": str(row.get("director") or "").strip() or None,
                "release_year": row.get("release_year"),
                # Raw Allocine display string ("2h 48min"); _parse_runtime handles the
                # contract's nullability, so no cleanup here.
                "runtime": row.get("runtime"),
            }
        )

    # Match against the existing cache before spending a Letterboxd search on it —
    # see _build_cache_index / _match_cache.
    films_to_search: list[dict] = []
    cache_matched = 0
    for film in films:
        slug = _match_cache(film, cache_index)
        if slug:
            cache_matched += 1
            logger.debug("cache match for %r → %s", film["title"], slug)
        else:
            films_to_search.append(film)
    logger.info(
        "%d/%d films matched directly against the cache; resolving the remaining %d against Letterboxd…",
        cache_matched,
        len(films),
        len(films_to_search),
    )

    slugs = asyncio.run(_resolve_all_slugs(films_to_search))
    resolved: list[str] = []
    unresolved: list[dict] = []
    for film, slug in zip(films_to_search, slugs, strict=True):
        if slug and slug not in cached_slugs:
            resolved.append(slug)
            cached_slugs.add(slug)
        elif slug:
            logger.debug("skipped (already cached): %s", slug)
        else:
            unresolved.append(
                {
                    "movie": film["title"],
                    "original_title": film["original_title"],
                    "director": film["director"],
                    "release_year": film["release_year"],
                }
            )

    logger.info("Resolved %d new slugs; %d unresolvable", len(resolved), len(unresolved))

    if resolved:
        cache_df = get_letterboxd_data(resolved, cache_path, api_key)
        if not cache_df.empty:
            # Stamp the rows this pipeline just added. `resolved` slugs were absent from
            # the cache (filtered against `cached_slugs` above) so they carry no prior
            # source — "allocine_showtimes" is written here and only here, never
            # overwriting a ratings/watchlist provenance set by the user-data pipeline.
            cache_df.loc[cache_df["slug"].isin(set(resolved)), "source"] = "allocine_showtimes"
            write_parquet_validated(
                cache_df, cache_path, required_columns=DATA_LETTERBOXD.required_columns, label="data_letterboxd"
            )

    pd.DataFrame(unresolved).to_parquet(unresolved_path, index=False)
    if unresolved:
        logger.warning("Wrote %d unresolved films to %s", len(unresolved), unresolved_path)
    else:
        logger.info("All films resolved — %s is empty", unresolved_path)
