"""
Pipeline orchestration for the cinema dashboard.

Runs the two data scrapers (Allocine showtimes + Letterboxd movies) in parallel,
only when their output files are considered stale.

Scraper argv lists and the staleness rules live in ``modules/scrapers.py`` — the
single source of truth shared with the Dagster pipeline (``pipeline/assets.py``).

Usage:
    python orchestrate.py            # refresh only stale data
    python orchestrate.py --force    # always re-run both scrapers
    python orchestrate.py --days 7   # forward --days flag to the Allocine scraper
"""

import asyncio
import logging
import os
from pathlib import Path

import click

from modules.config import settings
from modules.scrapers import (
    WATCHLIST_MAX_AGE_DAYS,
    _last_tuesday,
    _mtime,
    allocine_command,
    enrich_command,
    is_showtimes_stale,
    is_watchlist_stale,
    letterboxd_command,
)
from utils.streaming import refresh_streaming_providers

# ── Config ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# httpx logs every request at INFO; quiet it so the streaming refresh doesn't
# spam one line per TMDB call (can be thousands for a large watchlist).
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Staleness rules and scraper argv lists live in modules/scrapers.py — the
# single source of truth shared with the Dagster pipeline (pipeline/assets.py).

# ── Scraper runner ────────────────────────────────────────────────────────────


async def run_scraper(label: str, cmd: list[str], cwd: Path) -> bool:
    """Run a scraper subprocess, streaming its output prefixed with [label].

    Returns True on success (exit code 0), False otherwise.
    stderr is merged into stdout so all output is captured in order.
    """
    logger.info("[%s] Starting: %s", label, " ".join(cmd))
    # Strip VIRTUAL_ENV so `uv run` in the sibling repo doesn't warn about a
    # mismatched ancestor venv (cinema_dashboard's) — let it resolve its own.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            logger.info("[%s] %s", label, line.decode().rstrip())
        await proc.wait()
    except Exception as exc:
        logger.error("[%s] ERROR: %s", label, exc)
        return False

    if proc.returncode == 0:
        logger.info("[%s] Done.", label)
    else:
        logger.error("[%s] Failed (exit code %d).", label, proc.returncode)
    return proc.returncode == 0


async def _run_all(tasks: list[tuple[str, list[str], Path]]) -> dict[str, bool]:
    async with asyncio.TaskGroup() as tg:
        futures = {label: tg.create_task(run_scraper(label, cmd, cwd)) for label, cmd, cwd in tasks}
    return {label: task.result() for label, task in futures.items()}


def _log_result(label: str, ok: bool) -> None:
    """Log a one-line ✓/✗ summary for a finished scraper."""
    if ok:
        logger.info("  %-12s ✓ ok", label)
    else:
        logger.error("  %-12s ✗ failed", label)


# ── CLI ───────────────────────────────────────────────────────────────────────


@click.command()
@click.option("--force", is_flag=True, help="Re-run all scrapers regardless of staleness.")
@click.option("--days", default=14, show_default=True, help="Number of days to scrape for Allocine.")
@click.option("--reset", is_flag=True, help="Pass --reset to Allocine scraper (clears tmp cache).")
@click.option("--reset-db", is_flag=True, help="Pass --reset_database to movies_management.")
def main(force: bool, days: int, reset: bool, reset_db: bool) -> None:
    """Refresh cinema dashboard data (showtimes + watchlist)."""
    allocine_dir = settings.allocine_dir
    movies_dir = settings.movies_dir

    if not settings.allocine_output_path:
        raise click.ClickException("ALLOCINE_OUTPUT_PATH is not set in cinema_dashboard/.env")
    if not settings.movies_output_path:
        raise click.ClickException("MOVIES_OUTPUT_PATH is not set in cinema_dashboard/.env")

    showtimes_path = settings.allocine_output_path
    theaters_path = settings.allocine_input_path  # theaters.csv — re-scrape if it changed
    watchlist_path = settings.movies_output_path / "watchlist_with_letterboxd.parquet"

    # ── Decide which scrapers to run ──────────────────────────────────────────
    tasks: list[tuple[str, list[str], Path]] = []

    run_allocine = force or is_showtimes_stale(showtimes_path, theaters_path)
    run_watchlist = force or is_watchlist_stale(watchlist_path)

    if run_allocine:
        if force:
            reason = "forced"
        elif is_showtimes_stale(showtimes_path):  # weekly-programme rule or missing file
            reason = f"stale (last Tuesday: {_last_tuesday().strftime('%Y-%m-%d')})"
        else:
            reason = "theater list changed since last scrape"
        logger.info("Allocine showtimes: %s", reason)
        tasks.append(("allocine", allocine_command(days, reset), allocine_dir))
    else:
        mtime = _mtime(showtimes_path)
        logger.info("Allocine showtimes: fresh (last updated %s)", mtime.strftime("%Y-%m-%d %H:%M") if mtime else "never")

    if run_watchlist:
        reason = "forced" if force else f"stale (>{WATCHLIST_MAX_AGE_DAYS} days old)"
        logger.info("Letterboxd data:    %s", reason)
        if not settings.letterboxd_username:
            raise click.ClickException("LETTERBOXD_USERNAME is not set in cinema_dashboard/.env")
        tasks.append(("letterboxd", letterboxd_command(settings.letterboxd_username, reset_db), movies_dir))
    else:
        mtime = _mtime(watchlist_path)
        logger.info("Letterboxd data:    fresh (last updated %s)", mtime.strftime("%Y-%m-%d %H:%M") if mtime else "never")

    # ── Run in parallel ───────────────────────────────────────────────────────
    if tasks:
        logger.info("Running %d scraper(s) in parallel...", len(tasks))
        results = asyncio.run(_run_all(tasks))

        all_ok = True
        for label, ok in results.items():
            _log_result(label, ok)
            all_ok = all_ok and ok

        if not all_ok:
            raise click.ClickException("One or more scrapers failed.")
    else:
        logger.info("All scraper data is fresh. Use --force to re-run anyway.")
        results = {}

    # ── Allocine cache enrichment ─────────────────────────────────────────────
    # Expand data_letterboxd.parquet to include metadata for every film in the
    # fresh showtimes parquet, not only the user's watchlist and ratings.
    # Only runs when Allocine was refreshed (new showtimes data available).
    if run_allocine and results.get("allocine"):
        if not settings.letterboxd_username:
            logger.warning("LETTERBOXD_USERNAME not set — skipping Allocine cache enrichment")
        else:
            logger.info("Allocine scrape succeeded — running Letterboxd cache enrichment")
            ok = asyncio.run(run_scraper("enrich", enrich_command(str(showtimes_path)), movies_dir))
            _log_result("enrich", ok)

    # ── TMDB streaming-providers refresh ──────────────────────────────────────
    # In-repo Python (the streaming cache is dashboard-owned, like the geocode
    # cache) — not a sibling-repo subprocess. Incremental: skips rows fetched
    # < 7 days ago, so it is cheap to call on every run.
    if not settings.tmdb_api_key:
        logger.warning("TMDB_API_KEY not set — skipping streaming-providers refresh")
    else:
        logger.info("Refreshing TMDB watch providers (FR)…")
        summary = refresh_streaming_providers(
            movies_output=str(settings.movies_output_path),
            tmdb_api_key=settings.tmdb_api_key,
            force=force,
        )
        _log_result("streaming", summary.get("errors", 0) == 0)


if __name__ == "__main__":
    main()
