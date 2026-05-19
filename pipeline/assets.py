import subprocess
from pathlib import Path

import pandas as pd
from dagster import AssetExecutionContext, AutomationCondition, Config, MaterializeResult, MetadataValue, asset

from modules.scrapers import allocine_command, enrich_command, letterboxd_command

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
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

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
        raise RuntimeError("LETTERBOXD_USERNAME is not set in cinema_dashboard/.env")

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
