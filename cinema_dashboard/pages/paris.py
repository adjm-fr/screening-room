"""
Screening in Paris — everything screening this week, not just the watchlist.

Every other showtimes-driven page (Home, Watchlist Screenings) is built on
``sources.loader.build_watchlist_showtimes``, an inner join that only ever
surfaces films already on the watchlist. Measured against the real parquets:
250 films screen across 13 Paris theaters in a week and that join surfaces 14
of them. This page joins the full showtimes against the Letterboxd metadata
cache instead (``sources.discover.build_screenings``), labels every film
"new to you" / "watchlist" / "seen", and taste-ranks them with the same ranker
(``core.taste``) every other rail uses — so the badge and "because" chips mean
the same thing here as on Home.

The page is **one programme with three lenses** — no curated rails. The same
vertical agenda the Watchlist Screenings page renders is the whole page, and
the three questions the old rails answered ("what's new to me?", "what did I
dislike that my taste now says I'd like?", "what would I happily see again?")
are single-select lens chips that scope it: :func:`core.lenses.categorize`
assigns each row at most one ``_category`` (``"new"`` / ``"second_chance"`` /
``"rewatch"``), and ``ui.agenda`` renders a matching badge on every
categorised row, so the lenses stay visible even while browsing "All". The lens
vocabulary itself — the categories, their thresholds and the counts behind the
chips — lives in :mod:`core.lenses`, Streamlit-free; this module renders it.
Filtering is the calendar page's machinery verbatim —
:class:`core.agenda.AgendaFilters` + :func:`core.agenda.apply_filters` +
:func:`core.agenda.apply_day` — so there is exactly one filter chain and one
frame here too. What this page does *not* carry is the calendar's theater map or
its Agenda/Map view switcher.

It *does* carry an export, and it is deliberately the opposite of the calendar's.
There, "the export mirrors its on-screen filters" is structural: one frame feeds
both the agenda and the download, so picking a day scopes the ``.ics``. Here,
discovery ends in a decision, so **every showtime is a button**
(``ui.cart.render_plan_agenda``) and the picked screenings accumulate in a cart
that is *independent* of every filter, lens and day — a plan has to survive
changing the view that produced it. Do not "fix" that by narrowing the cart to
``filtered``; see ``core.cart``'s module docstring for the full reasoning, and
``ui.cart`` for why the cart, not the widget, is the source of truth.

**This page has no read-only agenda mode, on purpose.** It shipped with a "Plan"
toggle swapping ``ui.render_agenda`` for the picker, and the toggle was removed
because the two rendered the same facts: once the pills carried the theater name
(``ui.cart.pick_labels``) there was nothing browse mode showed that plan mode did
not, so the toggle was a mode with no information behind it. Measured on the real
programme, the picker costs +213ms per rerun over the static agenda for 417 rows
in the widest view — the price of the sticky day header
(``.agenda-day--plan`` unsticks it) and worth it for the page's whole point. The
calendar page still uses ``ui.render_agenda``; this one does not.

Three orderings are load-bearing:

- **the lens is a scoping step, not an ``AgendaFilters`` field.** The
  categories exist only on this page's frame (``watch_status`` and
  ``user_rating`` come from :func:`sources.discover.build_screenings`; the
  calendar's ``wl_shows`` never carries them), so folding them into the shared
  dataclass would push page-specific vocabulary into ``core.agenda``. The lens
  follows :func:`~core.agenda.apply_day`'s precedent instead — applied after
  ``apply_filters``, immediately before the day strip. Note this is an argument
  against ``core.agenda`` specifically, not against ``core``: the vocabulary
  itself lives in :mod:`core.lenses`, which shares no machinery with the filter
  chain.
- **the lens and the day strip scope only the agenda.** The KPI strip is
  computed on the post-``apply_filters``, pre-lens frame: it describes the
  whole week's programme, every lens included.
- **every widget key is namespaced ``paris_*``.** The calendar's controls use
  ``cal_*`` and both pages can live in one Streamlit session, so a shared key
  would silently make one page's filters follow the user onto the other.

A seen film that lands in neither "worth" lens — the ranker didn't flag it for
a second chance and it didn't clear the rewatch bar either — is dropped from
``narrowed`` outright, right after :func:`core.lenses.categorize` runs and before the KPI
strip. It is not merely hidden behind a lens: this page's whole premise is
"what in this week's programme is worth your time", and an already-seen film
neither disliked-but-now-a-match nor loved enough to revisit answers "no" to
that question regardless of which lens is selected.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from core.agenda import (
    RUNTIME_BUCKETS,
    TIME_BUCKET_LABELS,
    AgendaFilters,
    apply_day,
    apply_filters,
    build_agenda,
    day_chips,
)
from core.cart import ScreeningCart, cart_index, save_cart
from core.lenses import LENS_ALL, LENS_LABELS, categorize, drop_uninteresting_seen, lens_counts
from core.taste import TasteProfile, attach_match, build_affinity
from sources.discover import build_screenings
from sources.loader import future_showtimes, get_paths, load_letterboxd_cache, load_ratings, load_showtimes, load_watchlist
from ui import (
    cart_state,
    render_cart_panel,
    render_chip_filter,
    render_day_strip,
    render_empty_state,
    render_free_time_filter,
    render_kpi_strip,
    render_plan_agenda,
)

SORT_TIME = "⏱ Time"
SORT_MATCH = "◎ Match"


def _render_lens_strip(narrowed: pd.DataFrame) -> str:
    """Render the lens chips; return the selected lens value (:data:`LENS_ALL` = whole programme).

    Modelled on :func:`ui.agenda.render_day_strip`: the *options* are the
    stable category values and the label + count ride in ``format_func``, so
    the value stored under ``paris_lens`` is a stable scalar across reruns.
    (``ui.render_chip_filter`` does single-select but has no ``format_func``
    passthrough — baking counts into the option strings would reset the
    selection every time another filter changed a count.) Renders no control
    at all when no lens applies — a lone "All" chip is noise — and anything
    stale that no longer resolves to an offered option falls back to All:
    showing the whole programme beats showing an empty agenda.

    Every count is suffixed "film(s)": the day strip right below counts
    screenings instead (one film playing at three theaters is three day-strip
    screenings but one lens-strip film), and an unlabelled number here would
    read as the same measure landing on two different totals for the same
    word "All".
    """
    counts = lens_counts(narrowed)
    if not counts:
        return LENS_ALL

    def _films(n: int) -> str:
        return f"{n} film{'s' if n != 1 else ''}"

    total = int(narrowed["_film_key"].nunique())
    labels = {LENS_ALL: f"All · {_films(total)}"}
    labels |= {lens: f"{LENS_LABELS[lens]} · {_films(n)}" for lens, n in counts.items()}
    # Stretched, unlike the day strip: this strip holds at most four options, so
    # it fills one line rather than wrapping, and Streamlit's default full-width
    # segmented control is the look we want. The day strip has to opt out of it
    # (see ui.agenda.CHIP_STRIP_CONTAINER_PREFIX) only because its option count
    # grows with the data until it wraps.
    selection = st.segmented_control(
        "Lens",
        options=list(labels),
        selection_mode="single",
        default=LENS_ALL,
        format_func=lambda value: labels[value],
        key="paris_lens",
        label_visibility="collapsed",
        width="stretch",
    )
    return selection if isinstance(selection, str) and selection in labels else LENS_ALL


def _filters_badge() -> str:
    """Label for the Filters popover, counting the controls that are away from default.

    Read from session state (the ``paris_*`` keys) rather than from the live
    values, because the label has to be rendered *before* the popover's own
    widgets run. Streamlit reruns on every interaction, so the count the user
    actually sees is always current.
    """
    state = st.session_state
    prior = AgendaFilters(
        search=str(state.get("paris_search") or ""),
        theaters=tuple(state.get("paris_theaters") or ()),
        runtimes=tuple(state.get("paris_runtime") or ()),
        time_buckets=tuple(state.get("paris_tod") or ()),
        min_rating=float(state.get("paris_minrating") or 0.0),
        only_free=bool(state.get("paris_free")),
    )
    count = prior.active_count()
    return f"Filters · {count}" if count else "Filters"


def _render_toolbar(
    screenings: pd.DataFrame, *, has_profile: bool
) -> tuple[AgendaFilters, Literal["time", "match"], DeltaGenerator]:
    """Render every control; return the filters, the sort mode and the cart slot.

    Modelled on the calendar page's toolbar minus the Agenda/Map view switcher.
    The free-time selection is folded *into* :class:`~core.agenda.AgendaFilters`
    rather than applied on the spot, so it stays inside the one filter chain.

    The cart slot is an empty container returned to be filled *after* the agenda,
    the same deferred-container trick ``kpi_slot`` uses below and the calendar's
    export slot uses: the agenda reconciles the cart as it renders, so a popover
    filled here would show the previous interaction's count for one frame.
    """
    col_search, col_filters, col_sort, col_cart = st.columns([5, 2, 3, 2], vertical_alignment="bottom")

    with col_search:
        search = st.text_input(
            "Search title or director",
            key="paris_search",
            placeholder="🔍  Search title or director",
            label_visibility="collapsed",
        )

    with col_filters, st.popover(_filters_badge(), icon=":material/tune:", use_container_width=True):
        theaters = sorted(screenings["theater_name"].dropna().unique().tolist()) if "theater_name" in screenings.columns else []
        # No default: an empty selection means "all theaters", so the long list
        # stays inside the dropdown instead of rendering as a wall of tags.
        sel_theaters = st.multiselect("Theaters", theaters, key="paris_theaters", placeholder="All theaters")
        sel_runtime = render_chip_filter("Runtime", list(RUNTIME_BUCKETS), key="paris_runtime")
        min_rating = st.slider("Min Letterboxd rating", 0.0, 5.0, 0.0, 0.5, key="paris_minrating")

    sort_mode: Literal["time", "match"] = "time"
    if has_profile:
        with col_sort:
            # Built conditionally rather than disabled: with no ratings history
            # there is no Match to sort by, so render no control at all.
            choice = st.segmented_control(
                "Sort",
                [SORT_TIME, SORT_MATCH],
                default=SORT_TIME,
                key="paris_sort",
                label_visibility="collapsed",
                width="stretch",
            )
        sort_mode = "match" if choice == SORT_MATCH else "time"

    cart_slot = col_cart.container()

    sel_tod = render_chip_filter("Time of day", list(TIME_BUCKET_LABELS), key="paris_tod", label_visibility="collapsed")
    # Date options come from the unfiltered frame, so narrowing another filter
    # can't drop a date out from under the pickers.
    free_time = render_free_time_filter(screenings, key_prefix="paris")

    filters = AgendaFilters(
        search=search,
        theaters=tuple(sel_theaters),
        runtimes=tuple(sel_runtime),
        time_buckets=tuple(sel_tod),
        min_rating=min_rating,
        only_free=free_time.enabled,
        free_cutoff=free_time.cutoff,
        days_off=free_time.days_off,
        unavailable=free_time.unavailable,
    )
    return filters, sort_mode, cart_slot


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2.4rem;">Screening in Paris</h1>', unsafe_allow_html=True)
    st.caption(
        "Everything screening across your tracked theaters this week — not only your watchlist. "
        "One programme, three lenses: what's new to you, what deserves a second chance, what's worth a rewatch."
    )

    movies_path, showtimes_path, _ = get_paths()
    if not movies_path or not showtimes_path:
        render_empty_state(
            "⚙️",
            "Configure your data paths",
            "Set OUTPUT_PATH and ALLOCINE_OUTPUT_PATH in .env to populate the dashboard.",
        )
        return
    if not (movies_path / "data_letterboxd.parquet").exists() or not showtimes_path.exists():
        render_empty_state(
            "🎬",
            "No data yet",
            "Run the orchestrate.py CLI (or Dagster) to scrape the metadata cache + showtimes.",
        )
        return

    try:
        showtimes_df = load_showtimes(str(showtimes_path))
        cache_df = load_letterboxd_cache(str(movies_path))
        ratings_df = (
            load_ratings(str(movies_path)) if (movies_path / "ratings_with_letterboxd.parquet").exists() else pd.DataFrame()
        )
        watchlist_df = (
            load_watchlist(str(movies_path)) if (movies_path / "watchlist_with_letterboxd.parquet").exists() else pd.DataFrame()
        )
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    showtimes_df = future_showtimes(showtimes_df)
    if showtimes_df.empty:
        render_empty_state(
            "🍿",
            "No screenings this week",
            "Showtimes refresh Tuesday morning — check back soon.",
        )
        return

    screenings = build_screenings(showtimes_df, cache_df, ratings_df, watchlist_df)
    # Scored before any filtering: `match` then survives every narrowing for
    # free, and attach_match also carries back the metadata columns the
    # "because" chips need (see core.taste). Note it resets the index, so
    # nothing index-aligned may cross this line.
    profile: TasteProfile | None = build_affinity(ratings_df) if not ratings_df.empty else None
    has_profile = profile is not None and not profile.is_empty
    if has_profile and profile is not None:
        screenings = attach_match(screenings, cache_df, profile)

    # Filled after filtering, so the counts describe the frame the page shows.
    kpi_slot = st.container()

    filters, sort_mode, cart_slot = _render_toolbar(screenings, has_profile=has_profile)

    # The cart is loaded (and pruned of screenings that have already started)
    # before the programme renders, because plan mode reconciles into it as it
    # goes. The panel is filled *after*, on every exit path — a plan must not
    # lose its export button just because the current filters match nothing.
    cart = cart_state()
    _render_programme(
        screenings,
        filters=filters,
        sort_mode=sort_mode,
        profile=profile if has_profile else None,
        kpi_slot=kpi_slot,
        cart=cart,
    )
    with cart_slot:
        render_cart_panel(cart)


def _render_programme(
    screenings: pd.DataFrame,
    *,
    filters: AgendaFilters,
    sort_mode: Literal["time", "match"],
    profile: TasteProfile | None,
    kpi_slot: DeltaGenerator,
    cart: ScreeningCart,
) -> None:
    """Filter, categorise and render the week's programme (KPIs, lens, day, agenda).

    Split out of :func:`main` so its three "nothing matches" early returns cannot
    skip the cart panel — that panel shows the whole cart, which is exactly what a
    user needs when a filter has just emptied the screen.
    """
    # ── One filter chain, one frame ──────────────────────────────────────────
    # Same machinery as the Watchlist Screenings page: every control except the
    # lens and the day strip lands in AgendaFilters, and apply_filters is the
    # only place that narrows. Add a control by extending AgendaFilters, never
    # by filtering again below — that is how two surfaces on one page silently
    # diverge.
    narrowed = apply_filters(screenings, filters)
    if narrowed.empty:
        render_empty_state("🔍", "No matches", "Loosen the filters to see more screenings.")
        return

    # Categories assigned post-filter, so the lens chip counts below describe
    # the frame the page shows. `apply_filters` already derived `_film_key`
    # (via with_agenda_columns) — the slug-first film identity the KPI strip,
    # the lens counts and build_agenda's grouping all share.
    narrowed = narrowed.assign(_category=categorize(narrowed))
    narrowed = drop_uninteresting_seen(narrowed)
    if narrowed.empty:
        render_empty_state("🔍", "No matches", "Loosen the filters to see more screenings.")
        return

    # ── KPI strip ────────────────────────────────────────────────────────────
    # Watch-status counts, not core.agenda.agenda_kpis: the question this page
    # answers is "how much of the week's programme is new to me?". Computed on
    # `narrowed` — the whole week, every lens — never on the lensed frame.
    # "Already seen" only counts what survived the drop above, i.e. seen films
    # still worth a second chance or a rewatch.
    film_ids = narrowed["_film_key"]

    def _n_unique(status: str) -> int:
        return int(film_ids[narrowed["watch_status"] == status].nunique())

    with kpi_slot:
        render_kpi_strip(
            [
                ("Films screening", int(film_ids.nunique())),
                ("New to you", _n_unique("untracked")),
                ("On your watchlist", _n_unique("watchlist")),
                ("Already seen", _n_unique("seen")),
            ]
        )
        # An explicit spacer rather than st.write(""): an empty markdown block
        # collapses to a near-zero-height paragraph in current Streamlit, which
        # read as no gap at all between the KPI cards and the toolbar below.
        st.markdown('<div style="height: 1.5rem;"></div>', unsafe_allow_html=True)

    # ── Lenses over one programme ────────────────────────────────────────────
    # A scoping step like the day strip, deliberately NOT an AgendaFilters
    # field: the lens categories are a Paris-only concept (watch_status and
    # user_rating are build_screenings columns that don't exist on the calendar
    # page's wl_shows frame), so folding them into the shared dataclass would
    # push page vocabulary into core.agenda. Same precedent as apply_day.
    lens = _render_lens_strip(narrowed)
    lensed = narrowed if lens == LENS_ALL else narrowed[narrowed["_category"] == lens]

    # The day strip sits immediately below the lens chips and, like the lens,
    # scopes only the agenda — the KPI strip above stays week-wide.
    day = render_day_strip(day_chips(lensed), key="paris_day")
    filtered = apply_day(lensed, day)
    if filtered.empty:
        render_empty_state("🔍", "Nothing here", "Pick another day or lens, or go back to All.")
        return

    # The index is built from `filtered` — the frame actually on screen — so a
    # picked showtime carries the snapshot the ICS export needs without the cart
    # ever having to re-find its row.
    days = build_agenda(filtered, sort=sort_mode)
    if render_plan_agenda(days, profile=profile, cart=cart, index=cart_index(filtered)):
        save_cart(cart)


main()
