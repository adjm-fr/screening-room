"""
Public UI surface for the cinema dashboard.

Re-exports the full public API of ``ui.theme`` (CSS injection, formatting,
movie-detail links), ``ui.cards`` (movie cards, poster rails, hero cards),
``ui.chips`` (taste-match badges, filter pills, KPI strips, empty states,
freshness banner), and ``ui.ics`` (calendar export) so call sites can do
``from ui import (...)`` regardless of which submodule actually defines the
symbol.
"""

from ui.cards import render_compact_movie_card, render_hero_card, render_movie_card, render_poster_rail
from ui.chips import (
    match_chips_html,
    render_chip_filter,
    render_empty_state,
    render_freshness_banner,
    render_kpi_strip,
)
from ui.ics import ADS_MINUTES_CHAIN, ADS_MINUTES_DEFAULT, screening_end, to_ics
from ui.theme import MOVIE_QUERY_PARAM, format_runtime, inject_css, movie_href, rating_to_hsl, row_slug

__all__ = [
    "inject_css",
    "format_runtime",
    "rating_to_hsl",
    "movie_href",
    "row_slug",
    "MOVIE_QUERY_PARAM",
    "render_movie_card",
    "render_compact_movie_card",
    "render_poster_rail",
    "render_hero_card",
    "match_chips_html",
    "render_chip_filter",
    "render_kpi_strip",
    "render_empty_state",
    "render_freshness_banner",
    "screening_end",
    "to_ics",
    "ADS_MINUTES_CHAIN",
    "ADS_MINUTES_DEFAULT",
]
