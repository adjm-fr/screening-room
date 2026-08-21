"""
Calibration CLI for the Allocine↔Letterboxd match scorer: reports
precision/recall for the constants currently baked into ``modules.matching``
(default), or grid-searches candidate weights/accept/margin (``--sweep``).
See ``modules/match_calibration.py`` for the ground-truth derivation and
evaluation methodology (tier-1-derived positives, same-title-collision
negatives, grouped-by-film scoring).

Usage:
    python match_calibration.py --showtimes-path /path/to/showtimes.parquet
    python match_calibration.py --showtimes-path /path/to/showtimes.parquet --sweep
"""

import itertools
import logging
from pathlib import Path

import click
import pandas as pd
from common import configure_logging, secret_values
from common.parquet_io import read_parquet_validated
from contracts import SHOWTIMES
from modules import match_calibration as calibration
from modules import matching
from modules.config import Settings

settings = Settings()  # type: ignore[call-arg]
configure_logging("INFO", secrets=secret_values(settings))
logger = logging.getLogger(__name__)

# Grid swept by --sweep. ACCEPT/MARGIN are varied directly; WEIGHTS is varied only
# on its "year" entry (the term the exact-year-vs-Δ1-year precedence question turns
# on), every other WEIGHTS entry held at modules.matching.WEIGHTS's current value.
_ACCEPT_GRID = (0.65, 0.7, 0.75, 0.8, 0.85)
_MARGIN_GRID = (0.03, 0.05, 0.08, 0.12)
_YEAR_WEIGHT_GRID = (0.15, 0.2, 0.25, 0.3)


def _load_cache() -> pd.DataFrame:
    cache_path = Path(settings.output_path) / "data_letterboxd.parquet"
    logger.info("Loading cache from %s", cache_path)
    cache_df = pd.read_parquet(cache_path)
    logger.info("Cache loaded: %d rows", len(cache_df))
    return cache_df


def _print_metrics(metrics: calibration.Metrics) -> None:
    click.echo("Match scorer:")
    click.echo(f"  precision            = {metrics.precision:.4f}")
    click.echo(f"  recall               = {metrics.recall:.4f}")
    click.echo(f"  true_positives       = {metrics.true_positives}")
    click.echo(f"  wrong_picks          = {metrics.wrong_picks}")
    click.echo(f"  false_negatives      = {metrics.false_negatives}")
    click.echo(f"  total_positives      = {metrics.total_positives}")
    if metrics.wrong_pick_examples:
        click.echo("  wrong picks (title, correct_slug, picked_slug):")
        for title, correct_slug, picked_slug in metrics.wrong_pick_examples:
            click.echo(f"    {title!r}: {correct_slug} → {picked_slug}")


def _run_sweep(positives: list, negatives: list) -> None:
    header = (
        f"{'accept':>7}  {'margin':>7}  {'year_w':>7}  {'precision':>10}  {'recall':>8}  {'wrong_picks':>12}  {'false_neg':>10}"
    )
    click.echo(header)
    click.echo("-" * len(header))
    grid = []
    for accept, margin, year_w in itertools.product(_ACCEPT_GRID, _MARGIN_GRID, _YEAR_WEIGHT_GRID):
        weights = dict(matching.WEIGHTS)
        weights["year"] = year_w
        grid.append(calibration.SweepCandidate(weights=weights, accept=accept, margin=margin))

    for candidate, metrics in calibration.sweep(positives, negatives, grid):
        click.echo(
            f"{candidate.accept:>7.2f}  {candidate.margin:>7.2f}  {candidate.weights['year']:>7.2f}  "
            f"{metrics.precision:>10.4f}  {metrics.recall:>8.4f}  {metrics.wrong_picks:>12}  {metrics.false_negatives:>10}"
        )


@click.command()
@click.option(
    "--showtimes-path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to showtimes.parquet (the Allocine feed to derive ground truth from).",
)
@click.option("--sweep", is_flag=True, help="Grid-search ACCEPT / MARGIN / WEIGHTS['year'].")
def main(showtimes_path: Path, sweep: bool) -> None:
    """Evaluate (or sweep) the match-scorer constants against the real parquets."""
    showtimes_df = read_parquet_validated(showtimes_path, required_columns=SHOWTIMES.required_columns, label="showtimes")
    cache_df = _load_cache()

    positives, negatives = calibration.build_labeled_pairs(showtimes_df, cache_df)
    click.echo(f"Ground truth: {len(positives)} positives, {len(negatives)} negatives")

    if sweep:
        _run_sweep(positives, negatives)
        return

    metrics = calibration.evaluate(positives, negatives, weights=matching.WEIGHTS, accept=matching.ACCEPT, margin=matching.MARGIN)
    _print_metrics(metrics)


if __name__ == "__main__":
    main()
