import subprocess
from pathlib import Path

from dagster import AssetExecutionContext, AutomationCondition, Config, MaterializeResult, MetadataValue, asset

from .resources import ScraperConfig


class ShowtimesConfig(Config):
    days: int = 14
    reset: bool = False


class WatchlistConfig(Config):
    reset_db: bool = False


@asset(
    automation_condition=AutomationCondition.on_cron("0 6 * * 2"),  # Tuesday 06:00
    group_name="scrapers",
    description="Allocine showtimes parquet — refreshed every Tuesday.",
)
def showtimes(context: AssetExecutionContext, config: ShowtimesConfig, scraper_config: ScraperConfig) -> MaterializeResult:
    cmd = ["python", "main.py", "--days", str(config.days)]
    if config.reset:
        cmd.append("--reset")

    context.log.info("Running: %s (cwd=%s)", " ".join(cmd), scraper_config.allocine_dir)
    result = subprocess.run(cmd, cwd=scraper_config.allocine_dir, capture_output=True, text=True)

    for line in result.stdout.splitlines():
        context.log.info("[allocine] %s", line)
    for line in result.stderr.splitlines():
        context.log.warning("[allocine] %s", line)

    if result.returncode != 0:
        raise RuntimeError(f"Allocine scraper failed (exit {result.returncode})")

    output = Path(scraper_config.allocine_output_path)
    size_mb = output.stat().st_size / 1_000_000 if output.exists() else 0.0

    return MaterializeResult(
        metadata={
            "output_path": MetadataValue.path(scraper_config.allocine_output_path),
            "size_mb": MetadataValue.float(round(size_mb, 3)),
        }
    )


@asset(
    automation_condition=AutomationCondition.on_cron("0 6 * * 1"),  # Monday 06:00
    group_name="scrapers",
    description="Letterboxd watchlist parquet — refreshed weekly.",
)
def watchlist(context: AssetExecutionContext, config: WatchlistConfig, scraper_config: ScraperConfig) -> MaterializeResult:
    cmd = ["python", "main.py"]
    if config.reset_db:
        cmd.append("--reset_database")

    context.log.info("Running: %s (cwd=%s)", " ".join(cmd), scraper_config.movies_dir)
    result = subprocess.run(cmd, cwd=scraper_config.movies_dir, capture_output=True, text=True)

    for line in result.stdout.splitlines():
        context.log.info("[letterboxd] %s", line)
    for line in result.stderr.splitlines():
        context.log.warning("[letterboxd] %s", line)

    if result.returncode != 0:
        raise RuntimeError(f"Letterboxd scraper failed (exit {result.returncode})")

    output = Path(scraper_config.movies_output_path) / "watchlist_with_letterboxd.parquet"
    size_mb = output.stat().st_size / 1_000_000 if output.exists() else 0.0

    return MaterializeResult(
        metadata={
            "output_path": MetadataValue.path(str(output)),
            "size_mb": MetadataValue.float(round(size_mb, 3)),
        }
    )
