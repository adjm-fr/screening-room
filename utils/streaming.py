"""
TMDB watch-providers (France) data layer.

For every film on the watchlist we ask TMDB where it is streamable in France
right now (subscription / rent / buy) and persist the answer to a local cache
parquet, exactly mirroring the geocoding-cache pattern in ``utils/geo.py``:
fetch once, persist on disk, incremental-refresh on subsequent runs.

This module is **backend only** (Phase 2). It is filled by the data pipeline
(``orchestrate.py`` and the optional Dagster ``streaming_providers`` asset).
``load_streaming_providers`` is the read-only loader Phase 3 will join onto the
watchlist by ``tmdb_id`` to surface availability in the UI.

Public API:
    refresh_streaming_providers(*, movies_output, tmdb_api_key, ...) -> dict
    load_streaming_providers(movies_output) -> DataFrame

Cache file: ``data/streaming_providers.parquet`` — gitignored via the existing
``data/`` + ``*.parquet`` rules (same as ``data/theaters_geo.parquet``).
Refresh = re-run the pipeline; the 7-day incremental rule keeps it cheap.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

STREAMING_CACHE_PATH = Path("data") / "streaming_providers.parquet"
TMDB_PROVIDERS_URL = "https://api.themoviedb.org/3/movie/{tmdb_id}/watch/providers"
REQUEST_TIMEOUT = 10
# TMDB allows ~50 rps. 20 in-flight requests keeps us well under that while
# letting a multi-thousand-film watchlist finish in a sensible wall time.
MAX_CONCURRENCY = 20
_CACHE_COLUMNS = ["tmdb_id", "flatrate", "rent", "buy", "tmdb_link", "fetched_at"]


def _slugify(name: str) -> str:
    """Lowercase a provider name and drop every non-alphanumeric character.

    ``"Canal+" -> "canalplus"``, ``"Disney Plus" -> "disneyplus"``,
    ``"Amazon Prime Video" -> "amazonprimevideo"``. ``+`` maps to ``plus`` so
    ``"Canal+"`` and ``"Canal Plus"`` collapse to the same slug. Deliberately
    table-free; a curated alias map is deferred to Phase 3 if TMDB naming
    drift bites.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("+", "plus"))


def _parse_fr(payload: dict) -> dict:
    """Extract the France block from a TMDB watch/providers payload.

    The endpoint returns ``results`` keyed by ISO country code. Returns
    slugified provider-name lists for ``flatrate``/``rent``/``buy`` plus the
    FR JustWatch deep link. A missing ``FR`` key yields empty lists/link.
    """
    fr = payload.get("results", {}).get("FR", {})

    def _slugs(key: str) -> list[str]:
        return [_slugify(p["provider_name"]) for p in fr.get(key, []) if p.get("provider_name")]

    return {
        "flatrate": _slugs("flatrate"),
        "rent": _slugs("rent"),
        "buy": _slugs("buy"),
        "tmdb_link": str(fr.get("link", "")),
    }


def _read_cache(cache_path: Path) -> pd.DataFrame:
    """Read the streaming cache; return an empty typed frame if it's missing."""
    if cache_path.exists():
        return pd.read_parquet(cache_path)
    return pd.DataFrame(columns=_CACHE_COLUMNS).astype(
        {"tmdb_id": "string", "flatrate": "object", "rent": "object", "buy": "object", "tmdb_link": "string"}
    )


def _write_cache(df: pd.DataFrame, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)


def _watchlist_tmdb_ids(movies_output: str) -> list[str]:
    """Unique, non-empty ``tmdb_id`` strings from the watchlist parquet."""
    df = pd.read_parquet(Path(movies_output) / "watchlist_with_letterboxd.parquet", columns=["tmdb_id"])
    ids = df["tmdb_id"].dropna().astype(str).str.strip()
    return sorted({i for i in ids if i and i.lower() != "nan"})


async def _fetch_fr_providers(
    client: httpx.AsyncClient,
    tmdb_id: str,
    tmdb_api_key: str,
    sem: asyncio.Semaphore,
) -> tuple[str, dict | None]:
    """Fetch + parse the FR providers for one film. ``(tmdb_id, None)`` on any failure.

    The semaphore caps in-flight requests so a multi-thousand-film watchlist
    stays comfortably under TMDB's rate limit.
    """
    async with sem:
        try:
            resp = await client.get(
                TMDB_PROVIDERS_URL.format(tmdb_id=tmdb_id),
                params={"api_key": tmdb_api_key},
            )
        except httpx.HTTPError as exc:
            log.warning("TMDB providers request failed for tmdb_id=%s: %s", tmdb_id, exc)
            return tmdb_id, None
    if resp.status_code != 200:
        log.warning("TMDB providers returned HTTP %d for tmdb_id=%s", resp.status_code, tmdb_id)
        return tmdb_id, None
    return tmdb_id, _parse_fr(resp.json())


async def _fetch_all_fr_providers(
    tmdb_ids: list[str],
    tmdb_api_key: str,
    *,
    concurrency: int = MAX_CONCURRENCY,
) -> list[tuple[str, dict | None]]:
    """Fetch every ``tmdb_id`` concurrently, returning results in input order."""
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        return await asyncio.gather(*(_fetch_fr_providers(client, tid, tmdb_api_key, sem) for tid in tmdb_ids))


def refresh_streaming_providers(
    *,
    movies_output: str,
    tmdb_api_key: str | None,
    force: bool = False,
    cache_path: Path = STREAMING_CACHE_PATH,
    stale_after_days: int = 7,
) -> dict:
    """Refresh the FR streaming-providers cache for every watchlist ``tmdb_id``.

    Incremental: a cached row younger than ``stale_after_days`` is reused
    untouched unless ``force=True``. A single film's HTTP failure is logged
    and skipped — it never aborts the batch (mirrors ``geo.py``'s resilience).

    Returns a summary dict: ``fetched``, ``skipped_fresh``, ``errors`` — or
    ``{"skipped": True}`` when no API key is configured (graceful no-op,
    mirroring the enrich step skipping without ``LETTERBOXD_USERNAME``).
    """
    if not tmdb_api_key:
        log.warning("TMDB_API_KEY not set — skipping streaming-providers refresh")
        return {"skipped": True}

    tmdb_ids = _watchlist_tmdb_ids(movies_output)
    cache = _read_cache(cache_path)
    rows: dict[str, dict] = {r["tmdb_id"]: r for r in cache.to_dict("records")}

    now = datetime.now(UTC)
    fresh_cutoff = now - timedelta(days=stale_after_days)

    to_fetch: list[str] = []
    skipped_fresh = 0
    for tmdb_id in tmdb_ids:
        existing = rows.get(tmdb_id)
        if not force and existing is not None:
            raw_fetched_at = existing.get("fetched_at")
            if raw_fetched_at is not None:
                fetched_at = pd.to_datetime(raw_fetched_at, utc=True, errors="coerce")
                if pd.notna(fetched_at) and fetched_at >= fresh_cutoff:
                    skipped_fresh += 1
                    continue
        to_fetch.append(tmdb_id)

    results = asyncio.run(_fetch_all_fr_providers(to_fetch, tmdb_api_key)) if to_fetch else []

    fetched = errors = 0
    for tmdb_id, providers in results:
        if providers is None:
            errors += 1
            continue  # keep any stale row rather than dropping the film
        rows[tmdb_id] = {"tmdb_id": tmdb_id, **providers, "fetched_at": now}
        fetched += 1

    out = pd.DataFrame(list(rows.values()), columns=_CACHE_COLUMNS) if rows else _read_cache(cache_path)
    _write_cache(out, cache_path)

    summary = {"fetched": fetched, "skipped_fresh": skipped_fresh, "errors": errors}
    log.info(
        "Streaming providers: %d fetched, %d fresh-skipped, %d errors (%d films cached)",
        fetched,
        skipped_fresh,
        errors,
        len(out),
    )
    return summary


@st.cache_data(ttl=86400)
def load_streaming_providers(movies_output: str) -> pd.DataFrame:  # pragma: no cover
    """Read the FR streaming-providers cache (Phase 3 consumption point).

    Read-only, cached 24h. Takes ``str`` (not ``Path``) so the cache key
    stays stable across call sites, matching ``utils/data_loader.py``. The
    ``movies_output`` argument is accepted for call-site symmetry with the
    other loaders even though the cache lives in the dashboard's own
    ``data/`` dir. Returns an empty typed frame when the cache is absent.
    """
    log.debug("Loading streaming providers cache (movies_output=%s)", movies_output)
    df = _read_cache(STREAMING_CACHE_PATH)
    log.info("Streaming providers cache loaded: %d films", len(df))
    return df
