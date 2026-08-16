"""
Public UI surface for the cinema dashboard.

Re-exports the full public API of ``ui.theme`` (CSS injection, formatting,
movie-detail links), ``ui.cards`` (movie cards, poster rails, hero cards),
``ui.agenda`` (the calendar page's day sections and day strip), ``ui.chips``
(taste-match badges, filter pills, KPI strips, empty states, freshness banner),
``ui.availability`` (the "Only times I'm free" control), ``ui.cart`` (the Paris
page's plan mode and cart popover), and ``ui.ics`` (calendar export builders and
writer) so call sites can do ``from ui import (...)`` regardless of which
submodule actually defines the symbol.

``ui.cart`` itself must import its siblings as submodules (``from ui.agenda
import ...``), never through this package — the re-export below would make that
an import cycle.
"""

from ui.agenda import render_agenda, render_day_strip
from ui.availability import FreeTimeSelection, render_free_time_filter
from ui.cards import render_compact_movie_card, render_hero_card, render_movie_card, render_poster_rail
from ui.cart import cart_state, render_cart_panel, render_plan_agenda
from ui.chips import (
    match_chips_html,
    render_chip_filter,
    render_empty_state,
    render_freshness_banner,
    render_kpi_strip,
)
from ui.ics import ADS_MINUTES_CHAIN, ADS_MINUTES_DEFAULT, build_csv_rows, build_ics_events, screening_end, to_ics
from ui.stats import decade_profile_html, rating_histogram_html
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
    "render_agenda",
    "render_day_strip",
    "cart_state",
    "render_plan_agenda",
    "render_cart_panel",
    "match_chips_html",
    "rating_histogram_html",
    "decade_profile_html",
    "render_chip_filter",
    "render_kpi_strip",
    "render_empty_state",
    "render_freshness_banner",
    "FreeTimeSelection",
    "render_free_time_filter",
    "screening_end",
    "to_ics",
    "build_ics_events",
    "build_csv_rows",
    "ADS_MINUTES_CHAIN",
    "ADS_MINUTES_DEFAULT",
]
