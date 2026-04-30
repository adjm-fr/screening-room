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

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

ALLOCINE_DIR = _ROOT.parent / "Allocine-Showtimes-Scraping"
MOVIES_DIR = _ROOT.parent / "movies_management"

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
    print(f"[{label}] Starting: {' '.join(cmd)}", flush=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            print(f"[{label}] {line.decode().rstrip()}", flush=True)
        await proc.wait()
    except Exception as exc:
        print(f"[{label}] ERROR: {exc}", flush=True)
        return False

    if proc.returncode == 0:
        print(f"[{label}] Done.", flush=True)
    else:
        print(f"[{label}] Failed (exit code {proc.returncode}).", flush=True)
    return proc.returncode == 0


async def _run_all(tasks: list[tuple[str, list[str], Path]]) -> dict[str, bool]:
    async with asyncio.TaskGroup() as tg:
        futures = {label: tg.create_task(run_scraper(label, cmd, cwd)) for label, cmd, cwd in tasks}
    return {label: task.result() for label, task in futures.items()}


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh cinema dashboard data.")
    parser.add_argument("--force", action="store_true", help="Re-run all scrapers regardless of staleness.")
    parser.add_argument("--days", type=int, default=14, help="Number of days to scrape for Allocine (default: 14).")
    parser.add_argument("--reset", action="store_true", help="Pass --reset to Allocine scraper (clears tmp cache).")
    parser.add_argument("--reset-db", action="store_true", help="Pass --reset_database to movies_management.")
    return parser.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    import os

    args = _parse_args()

    showtimes_raw = os.getenv("ALLOCINE_OUTPUT_PATH")
    watchlist_raw = os.getenv("MOVIES_OUTPUT_PATH")

    if not showtimes_raw:
        print("ERROR: ALLOCINE_OUTPUT_PATH is not set in cinema_dashboard/.env", file=sys.stderr)
        return 1
    if not watchlist_raw:
        print("ERROR: MOVIES_OUTPUT_PATH is not set in cinema_dashboard/.env", file=sys.stderr)
        return 1

    showtimes_path = Path(showtimes_raw)
    watchlist_path = Path(watchlist_raw) / "watchlist_with_letterboxd.parquet"

    # ── Decide which scrapers to run ──────────────────────────────────────────
    tasks: list[tuple[str, list[str], Path]] = []

    allocine_stale = is_showtimes_stale(showtimes_path)
    watchlist_stale = is_watchlist_stale(watchlist_path)

    if args.force or allocine_stale:
        reason = "forced" if args.force else f"stale (last Tuesday: {_last_tuesday().strftime('%Y-%m-%d')})"
        print(f"Allocine showtimes: {reason}")
        allocine_cmd = ["python", "main.py", "--days", str(args.days)]
        if args.reset:
            allocine_cmd.append("--reset")
        tasks.append(("allocine", allocine_cmd, ALLOCINE_DIR))
    else:
        mtime = _mtime(showtimes_path)
        print(f"Allocine showtimes: fresh (last updated {mtime.strftime('%Y-%m-%d %H:%M') if mtime else 'never'})")

    if args.force or watchlist_stale:
        reason = "forced" if args.force else f"stale (>{WATCHLIST_MAX_AGE_DAYS} days old)"
        print(f"Letterboxd data:    {reason}")
        letterboxd_cmd = ["python", "main.py"]
        if args.reset_db:
            letterboxd_cmd.append("--reset_database")
        tasks.append(("letterboxd", letterboxd_cmd, MOVIES_DIR))
    else:
        mtime = _mtime(watchlist_path)
        print(f"Letterboxd data:    fresh (last updated {mtime.strftime('%Y-%m-%d %H:%M') if mtime else 'never'})")

    if not tasks:
        print("\nAll data is fresh. Use --force to re-run anyway.")
        return 0

    print(f"\nRunning {len(tasks)} scraper(s) in parallel...\n")

    # ── Run in parallel ───────────────────────────────────────────────────────
    results = asyncio.run(_run_all(tasks))

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────")
    all_ok = True
    for label, ok in results.items():
        status = "✓ ok" if ok else "✗ failed"
        print(f"  {label:12} {status}")
        if not ok:
            all_ok = False
    print()

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
