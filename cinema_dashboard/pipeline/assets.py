import os
import subprocess
from pathlib import Path

import pandas as pd
from dagster import AssetExecutionContext, AutomationCondition, Config, MaterializeResult, MetadataValue, asset
from modules.scrapers import allocine_command, enrich_command, letterboxd_command
from utils.streaming import refresh_streaming_providers

from .resources import ScraperConfig


class ShowtimesConfig(Config):
    days: int = 14
    reset: bool = False


class WatchlistConfig(Config):
    reset_db: bool = False


def _run(context: AssetExecutionContext, label: str, cmd: list[str], cwd: str) -> None:
    """Run a scraper subprocess, mirroring stdout/stderr into the Dagster log.

    Raises RuntimeError on a non-zero exit code.
    """
    context.log.info("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    # Strip VIRTUAL_ENV so `uv run` in the sibling repo doesn't warn about a
    # mismatched ancestor venv — let it resolve its own.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)

    for line in result.stdout.splitlines():
        context.log.info("[%s] %s", label, line)
    for line in result.stderr.splitlines():
        context.log.warning("[%s] %s", label, line)

    if result.returncode != 0:
        raise RuntimeError(f"{label} scraper failed (exit {result.returncode})")


@asset(
    automation_condition=AutomationCondition.on_cron("0 6 * * 2"),  # Tuesday 06:00
    group_name="scrapers",
    description="Allocine showtimes parquet — refreshed every Tuesday.",
)
def showtimes(context: AssetExecutionContext, config: ShowtimesConfig, scraper_config: ScraperConfig) -> MaterializeResult:
    _run(context, "allocine", allocine_command(config.days, config.reset), scraper_config.allocine_dir)

    output = Path(scraper_config.allocine_output_path)
    size_mb = output.stat().st_size / 1_000_000 if output.exists() else 0.0

    return MaterializeResult(
        metadata={
            "output_path": MetadataValue.path(scraper_config.allocine_output_path),
            "size_mb": MetadataValue.float(round(size_mb, 3)),
        }
    )


@asset(
    deps=["showtimes"],
    group_name="scrapers",
    description="Letterboxd metadata cache enriched with all films from the latest showtimes parquet.",
)
def letterboxd_cache_enriched(context: AssetExecutionContext, scraper_config: ScraperConfig) -> MaterializeResult:
    _run(context, "enrich", enrich_command(scraper_config.allocine_output_path), scraper_config.movies_dir)

    unresolved = Path(scraper_config.movies_output_path) / "unresolved_allocine.parquet"
    unresolved_count = len(pd.read_parquet(unresolved)) if unresolved.exists() else 0

    return MaterializeResult(
        metadata={
            "unresolved_count": MetadataValue.int(unresolved_count),
        }
    )


@asset(
    automation_condition=AutomationCondition.on_cron("0 6 * * 1"),  # Monday 06:00
    group_name="scrapers",
    description="Letterboxd watchlist parquet — refreshed weekly.",
)
def watchlist(context: AssetExecutionContext, config: WatchlistConfig, scraper_config: ScraperConfig) -> MaterializeResult:
    if not scraper_config.letterboxd_username:
        raise RuntimeError("LETTERBOXD_USERNAME is not set in the workspace-root .env")

    _run(
        context,
        "letterboxd",
        letterboxd_command(scraper_config.letterboxd_username, config.reset_db),
        scraper_config.movies_dir,
    )

    output = Path(scraper_config.movies_output_path) / "watchlist_with_letterboxd.parquet"
    size_mb = output.stat().st_size / 1_000_000 if output.exists() else 0.0

    return MaterializeResult(
        metadata={
            "output_path": MetadataValue.path(str(output)),
            "size_mb": MetadataValue.float(round(size_mb, 3)),
        }
    )


@asset(
    deps=["watchlist"],
    automation_condition=AutomationCondition.on_cron("0 7 * * 1"),  # Monday 07:00, after the watchlist asset
    group_name="scrapers",
    description="FR streaming-availability cache for every watchlist film (TMDB watch/providers).",
)
def streaming_providers(context: AssetExecutionContext, scraper_config: ScraperConfig) -> MaterializeResult:
    # In-repo Python (dashboard-owned cache) — not a sibling-repo subprocess,
    # so this calls the function directly rather than the _run helper.
    summary = refresh_streaming_providers(
        movies_output=scraper_config.movies_output_path,
        tmdb_api_key=scraper_config.tmdb_api_key or None,
        force=False,
    )
    context.log.info("Streaming providers refresh: %s", summary)

    return MaterializeResult(
        metadata={
            "fetched": MetadataValue.int(int(summary.get("fetched", 0))),
            "skipped_fresh": MetadataValue.int(int(summary.get("skipped_fresh", 0))),
            "errors": MetadataValue.int(int(summary.get("errors", 0))),
            "skipped": MetadataValue.bool(bool(summary.get("skipped", False))),
        }
    )
