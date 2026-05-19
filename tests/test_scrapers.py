"""Unit tests for modules.scrapers (command builders + staleness rules)."""

import os
from datetime import datetime, timedelta
from pathlib import Path

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

# ── Command builders ──────────────────────────────────────────────────────────


def test_allocine_command_without_reset():
    assert allocine_command(14, reset=False) == ["uv", "run", "python", "main.py", "--days", "14"]


def test_allocine_command_with_reset():
    assert allocine_command(7, reset=True) == ["uv", "run", "python", "main.py", "--days", "7", "--reset"]


def test_letterboxd_command_without_reset_db():
    assert letterboxd_command("alice", reset_db=False) == ["uv", "run", "python", "main.py", "--username", "alice"]


def test_letterboxd_command_with_reset_db():
    assert letterboxd_command("alice", reset_db=True) == [
        "uv",
        "run",
        "python",
        "main.py",
        "--username",
        "alice",
        "--reset_database",
    ]


def test_enrich_command():
    assert enrich_command("/data/showtimes.parquet") == [
        "uv",
        "run",
        "python",
        "main.py",
        "--enrich-from-allocine",
        "/data/showtimes.parquet",
    ]


# ── _last_tuesday ─────────────────────────────────────────────────────────────


def test_last_tuesday_is_a_tuesday_at_midnight():
    # 2026-05-20 is a Wednesday
    result = _last_tuesday(datetime(2026, 5, 20, 15, 30))
    assert result == datetime(2026, 5, 19, 0, 0)  # the day before
    assert result.weekday() == 1


def test_last_tuesday_returns_today_when_today_is_tuesday():
    tuesday = datetime(2026, 5, 19, 23, 59)
    assert _last_tuesday(tuesday) == datetime(2026, 5, 19, 0, 0)


# ── _mtime ────────────────────────────────────────────────────────────────────


def test_mtime_missing_file_returns_none(tmp_path: Path):
    assert _mtime(tmp_path / "nope.parquet") is None


def test_mtime_existing_file(tmp_path: Path):
    f = tmp_path / "f.parquet"
    f.write_text("x")
    assert isinstance(_mtime(f), datetime)


# ── Staleness helpers ─────────────────────────────────────────────────────────


def _touch(path: Path, when: datetime) -> None:
    path.write_text("x")
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_showtimes_stale_when_missing(tmp_path: Path):
    assert is_showtimes_stale(tmp_path / "missing.parquet") is True


def test_showtimes_stale_before_last_tuesday(tmp_path: Path):
    now = datetime(2026, 5, 20, 12, 0)  # Wednesday → last Tuesday is 2026-05-19
    f = tmp_path / "showtimes.parquet"
    _touch(f, datetime(2026, 5, 18, 12, 0))  # Monday, before last Tuesday
    assert is_showtimes_stale(f, now=now) is True


def test_showtimes_fresh_after_last_tuesday(tmp_path: Path):
    now = datetime(2026, 5, 20, 12, 0)
    f = tmp_path / "showtimes.parquet"
    _touch(f, datetime(2026, 5, 19, 9, 0))  # after last Tuesday 00:00
    assert is_showtimes_stale(f, now=now) is False


def test_showtimes_boundary_exactly_last_tuesday_is_fresh(tmp_path: Path):
    now = datetime(2026, 5, 20, 12, 0)
    f = tmp_path / "showtimes.parquet"
    _touch(f, datetime(2026, 5, 19, 0, 0))  # exactly the cutoff — strict `<`
    assert is_showtimes_stale(f, now=now) is False


def test_watchlist_stale_when_missing(tmp_path: Path):
    assert is_watchlist_stale(tmp_path / "missing.parquet") is True


def test_watchlist_fresh_within_max_age(tmp_path: Path):
    now = datetime(2026, 5, 20, 12, 0)
    f = tmp_path / "watchlist.parquet"
    _touch(f, now - timedelta(days=WATCHLIST_MAX_AGE_DAYS - 1))
    assert is_watchlist_stale(f, now=now) is False


def test_watchlist_stale_at_exactly_max_age(tmp_path: Path):
    now = datetime(2026, 5, 20, 12, 0)
    f = tmp_path / "watchlist.parquet"
    _touch(f, now - timedelta(days=WATCHLIST_MAX_AGE_DAYS))
    assert is_watchlist_stale(f, now=now) is True
