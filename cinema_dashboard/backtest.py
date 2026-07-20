"""
Backtest CLI for the taste ranker: reports held-out Spearman correlation and
quartile lift for the current ``utils.taste`` constants (default), or
grid-searches candidate values (``--sweep``). See ``utils/backtest.py`` for
the evaluation methodology (repeated random holdout, raw pre-logistic
scores, quantile-based lift).

Usage:
    python backtest.py            # metrics for the current taste.py constants
    python backtest.py --sweep    # grid-search SHRINKAGE_K / cast-weight / QUALITY_WEIGHT
"""

import itertools
import logging
from pathlib import Path

import click
import pandas as pd
from common import configure_logging
from modules.config import settings
from utils import backtest as backtest_utils
from utils import taste

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

# Grid swept by --sweep. Only the "cast" entry of WEIGHTS is varied; every
# other WEIGHTS entry is held at its current utils.taste.WEIGHTS value.
_SHRINKAGE_K_GRID = (2.0, 5.0, 10.0)
_CAST_WEIGHT_GRID = (0.2, 0.4, 0.6)
_QUALITY_WEIGHT_GRID = (0.1, 0.2, 0.3)


def _load_ratings() -> pd.DataFrame:
    if not settings.movies_output_path:
        raise click.ClickException("OUTPUT_PATH is not set in the workspace-root .env")
    path = Path(settings.movies_output_path) / "ratings_with_letterboxd.parquet"
    logger.info("Loading ratings from %s", path)
    df = pd.read_parquet(path)
    logger.info("Ratings loaded: %d rows", len(df))
    return df


def _print_metrics(metrics: dict[str, float]) -> None:
    click.echo("Taste ranker:")
    click.echo(f"  spearman        = {metrics['spearman']:.4f}")
    click.echo(f"  quartile_lift   = {metrics['quartile_lift']:.4f}")
    click.echo("Baseline (quality prior only):")
    click.echo(f"  baseline_spearman      = {metrics['baseline_spearman']:.4f}")
    click.echo(f"  baseline_quartile_lift = {metrics['baseline_quartile_lift']:.4f}")


def _run_sweep(ratings_df: pd.DataFrame) -> None:
    header = (
        f"{'shrinkage_k':>11}  {'cast_weight':>11}  {'quality_weight':>14}  "
        f"{'spearman':>9}  {'quartile_lift':>13}  {'base_spearman':>13}  {'base_lift':>10}"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for shrinkage_k, cast_weight, quality_weight in itertools.product(_SHRINKAGE_K_GRID, _CAST_WEIGHT_GRID, _QUALITY_WEIGHT_GRID):
        weights = dict(taste.WEIGHTS)
        weights["cast"] = cast_weight
        metrics = backtest_utils.evaluate(
            ratings_df,
            shrinkage_k=shrinkage_k,
            weights=weights,
            quality_weight=quality_weight,
        )
        click.echo(
            f"{shrinkage_k:>11.1f}  {cast_weight:>11.1f}  {quality_weight:>14.1f}  "
            f"{metrics['spearman']:>9.4f}  {metrics['quartile_lift']:>13.4f}  "
            f"{metrics['baseline_spearman']:>13.4f}  {metrics['baseline_quartile_lift']:>10.4f}"
        )


@click.command()
@click.option("--sweep", is_flag=True, help="Grid-search SHRINKAGE_K, WEIGHTS['cast'], and QUALITY_WEIGHT.")
def main(sweep: bool) -> None:
    """Evaluate (or sweep) the taste-ranker constants against held-out ratings."""
    ratings_df = _load_ratings()

    if sweep:
        _run_sweep(ratings_df)
        return

    metrics = backtest_utils.evaluate(
        ratings_df,
        shrinkage_k=taste.SHRINKAGE_K,
        weights=taste.WEIGHTS,
        quality_weight=taste.QUALITY_WEIGHT,
    )
    _print_metrics(metrics)


if __name__ == "__main__":
    main()
