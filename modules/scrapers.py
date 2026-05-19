"""
Shared scraper definitions for the cinema dashboard.

Single source of truth for the two pipeline runners (``orchestrate.py`` async
CLI and ``pipeline/assets.py`` Dagster) so the scraper subprocess argv lists
and the staleness rules live in exactly one place. The runners differ
intentionally (async-parallel vs Dagster sync-per-asset); only this pure
command/staleness logic is shared.

Command builders return the argv list passed to the sibling repos' ``main.py``.
Staleness helpers take an optional ``now`` so the clock can be pinned in tests.

Staleness rules:
  - showtimes.parquet : stale if last modified before the most recent Tuesday
                        00:00 (French cinemas publish the weekly programme on
                        Tuesdays)
  - watchlist parquet : stale if older than WATCHLIST_MAX_AGE_DAYS days
"""

from datetime import datetime, timedelta
from pathlib import Path

WATCHLIST_MAX_AGE_DAYS = 7

# ── Command builders ──────────────────────────────────────────────────────────


def allocine_command(days: int, reset: bool) -> list[str]:
    """Argv for the Allocine showtimes scraper (run with cwd=allocine_dir)."""
    cmd = ["uv", "run", "python", "main.py", "--days", str(days)]
    if reset:
        cmd.append("--reset")
    return cmd


def letterboxd_command(username: str, reset_db: bool) -> list[str]:
    """Argv for the Letterboxd watchlist scraper (run with cwd=movies_dir)."""
    cmd = ["uv", "run", "python", "main.py", "--username", username]
    if reset_db:
        cmd.append("--reset_database")
    return cmd


def enrich_command(showtimes_path: str) -> list[str]:
    """Argv for the Letterboxd cache enrichment step (run with cwd=movies_dir)."""
    return ["uv", "run", "python", "main.py", "--enrich-from-allocine", showtimes_path]


# ── Staleness helpers ─────────────────────────────────────────────────────────


def _last_tuesday(now: datetime | None = None) -> datetime:
    """Return the most recent Tuesday at 00:00 (today if today is Tuesday)."""
    today = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    # Monday=0 … Sunday=6, so Tuesday=1
    days_back = (today.weekday() - 1) % 7
    return today - timedelta(days=days_back)


def _mtime(path: Path) -> datetime | None:
    """Return the file's last-modified datetime, or None if the file doesn't exist."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


def is_showtimes_stale(path: Path, now: datetime | None = None) -> bool:
    """True if showtimes.parquet was last written before the most recent Tuesday."""
    mtime = _mtime(path)
    if mtime is None:
        return True
    return mtime < _last_tuesday(now)


def is_watchlist_stale(path: Path, now: datetime | None = None) -> bool:
    """True if watchlist parquet is older than WATCHLIST_MAX_AGE_DAYS days."""
    mtime = _mtime(path)
    if mtime is None:
        return True
    return ((now or datetime.now()) - mtime).days >= WATCHLIST_MAX_AGE_DAYS
