"""
Pipeline orchestration for the cinema dashboard.

Runs the two data scrapers (Allocine showtimes + Letterboxd movies) in parallel,
only when their output files are considered stale.

Staleness rules:
  - showtimes.parquet   : stale if last modified before the most recent Tuesday 00:00
                          (French cinemas publish the new weekly programme on Tuesdays)
  - watchlist parquet   : stale if older than 7 days

Usage:
    python orchestrate.py            # refresh only stale data
    python orchestrate.py --force    # always re-run both scrapers
    python orchestrate.py --days 7   # forward --days flag to the Allocine scraper
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import click

from modules.config import settings

# ── Config ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

WATCHLIST_MAX_AGE_DAYS = 7

# ── Staleness helpers ─────────────────────────────────────────────────────────


def _last_tuesday() -> datetime:
    """Return the most recent Tuesday at 00:00 (today if today is Tuesday)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # Monday=0 … Sunday=6, so Tuesday=1
    days_back = (today.weekday() - 1) % 7
    return today - timedelta(days=days_back)


def _mtime(path: Path) -> datetime | None:
    """Return the file's last-modified datetime, or None if the file doesn't exist."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


def is_showtimes_stale(path: Path) -> bool:
    """True if showtimes.parquet was last written before the most recent Tuesday."""
    mtime = _mtime(path)
    if mtime is None:
        return True
    return mtime < _last_tuesday()


def is_watchlist_stale(path: Path) -> bool:
    """True if watchlist parquet is older than WATCHLIST_MAX_AGE_DAYS days."""
    mtime = _mtime(path)
    if mtime is None:
        return True
    return (datetime.now() - mtime).days >= WATCHLIST_MAX_AGE_DAYS


# ── Scraper runner ────────────────────────────────────────────────────────────


async def run_scraper(label: str, cmd: list[str], cwd: Path) -> bool:
    """Run a scraper subprocess, streaming its output prefixed with [label].

    Returns True on success (exit code 0), False otherwise.
    stderr is merged into stdout so all output is captured in order.
    """
    logger.info("[%s] Starting: %s", label, " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
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
    watchlist_path = settings.movies_output_path / "watchlist_with_letterboxd.parquet"

    # ── Decide which scrapers to run ──────────────────────────────────────────
    tasks: list[tuple[str, list[str], Path]] = []

    allocine_stale = is_showtimes_stale(showtimes_path)
    watchlist_stale = is_watchlist_stale(watchlist_path)

    if force or allocine_stale:
        reason = "forced" if force else f"stale (last Tuesday: {_last_tuesday().strftime('%Y-%m-%d')})"
        logger.info("Allocine showtimes: %s", reason)
        allocine_cmd = ["python", "main.py", "--days", str(days)]
        if reset:
            allocine_cmd.append("--reset")
        tasks.append(("allocine", allocine_cmd, allocine_dir))
    else:
        mtime = _mtime(showtimes_path)
        logger.info("Allocine showtimes: fresh (last updated %s)", mtime.strftime("%Y-%m-%d %H:%M") if mtime else "never")

    if force or watchlist_stale:
        reason = "forced" if force else f"stale (>{WATCHLIST_MAX_AGE_DAYS} days old)"
        logger.info("Letterboxd data:    %s", reason)
        letterboxd_cmd = ["python", "main.py"]
        if reset_db:
            letterboxd_cmd.append("--reset_database")
        tasks.append(("letterboxd", letterboxd_cmd, movies_dir))
    else:
        mtime = _mtime(watchlist_path)
        logger.info("Letterboxd data:    fresh (last updated %s)", mtime.strftime("%Y-%m-%d %H:%M") if mtime else "never")

    if not tasks:
        logger.info("All data is fresh. Use --force to re-run anyway.")
        return

    logger.info("Running %d scraper(s) in parallel...", len(tasks))

    # ── Run in parallel ───────────────────────────────────────────────────────
    results = asyncio.run(_run_all(tasks))

    # ── Summary ───────────────────────────────────────────────────────────────
    all_ok = True
    for label, ok in results.items():
        if ok:
            logger.info("  %-12s ✓ ok", label)
        else:
            logger.error("  %-12s ✗ failed", label)
            all_ok = False

    if not all_ok:
        raise click.ClickException("One or more scrapers failed.")


if __name__ == "__main__":
    main()
