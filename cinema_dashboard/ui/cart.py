"""
The Streamlit surface of :mod:`core.cart`: plan mode, and the cart popover.

Two renderers, one cart:

- :func:`render_plan_agenda` is the agenda with each film's showtimes as a real
  ``st.pills`` widget instead of static ``.time-pill`` spans. It is a *second*
  renderer beside ``ui.agenda.render_agenda`` rather than a flag on it, because a
  widget cannot live inside a markdown blob and that blob is what makes the day
  header sticky (see ``ui.agenda``'s module docstring). It knowingly trades that
  sticky header for selectable showtimes, and it is the **only** agenda the Paris
  page renders — the toggle that used to pick between the two was removed once the
  pills carried venue names, because at that point the modes showed identical
  facts. ``ui.agenda.render_agenda`` lives on for the calendar page.
- :func:`render_cart_panel` is the toolbar popover: what is in the plan, the
  ``.ics`` download, remove-one and clear-all.

**The cart is the source of truth; the pills are a view of it.** Streamlit honours
``default=`` only on a widget's first run and drops a stored selection when the
widget stops rendering (``st.pills``' ``persist_state`` defaults to ``None``), so
the widget can never be where a plan lives. Everything that follows —
``core.cart.pick_group_key`` hashing the option universe, seeding ``default=``
from the cart on every render, and :func:`_drop_pick_widgets` sweeping keys by
prefix — exists to keep that direction one-way.

Imports here name ``ui`` *submodules* directly (``from ui.agenda import ...``),
never ``from ui import ...``: the package ``__init__`` re-exports this module, so
going through it would be an immediate import cycle.
"""

from __future__ import annotations

import html
from typing import cast

import pandas as pd
import streamlit as st

from core.agenda import AgendaDay, AgendaEntry
from core.cart import (
    CartItem,
    ScreeningCart,
    cart_frame,
    entry_showtime_ids,
    load_cart,
    pick_group_key,
    prune_past,
    reconcile_group,
    save_cart,
    showtime_id,
)
from core.taste import TasteProfile
from sources.loader import _now_paris
from ui.agenda import _agenda_day_head_html, _agenda_row_html
from ui.cards import _title_of
from ui.ics import build_ics_events, to_ics
from ui.theme import movie_href

#: Where the cart lives in session state. Deliberately *not* prefixed with
#: :data:`PICK_KEY_PREFIX` — the sweep below is keyed on that prefix and must
#: never reach the cart itself.
CART_SESSION_KEY = "paris_cart"

#: Prefix on every pills widget key this module mounts. ``paris_*`` because both
#: Paris and the calendar page can live in one session (see ``pages.paris``'s
#: docstring); the sweep in :func:`_drop_pick_widgets` depends on it.
PICK_KEY_PREFIX = "paris_cartpick_"

#: Prefix on the container each pills group is wrapped in. ``st.container(key=X)``
#: renders as ``.st-key-X``, which is how ``assets/styles.css`` reaches the
#: widget — the same trick ``ui.agenda.CHIP_STRIP_CONTAINER_PREFIX`` uses.
PICK_CONTAINER_PREFIX = "cartpick-"

#: Column split for a row: the film, then its showtimes. Nearly even because each
#: pill carries a venue as well as a time ("19:00 · Le Champo - Espace Jacques
#: Tati" — the real theater names run to 31 characters), so the picker needs
#: roughly as much width as the film does. Don't starve the left column further:
#: the row's own grid is ``56px | 1fr | max-content`` and the match block is
#: ``max-content``, so it squeezes the *title* rather than itself. Streamlit
#: stacks columns below ~640px, so the pills reflow under the row on mobile with
#: no media query of their own.
_PLAN_COLUMNS = (5, 4)


def cart_state() -> ScreeningCart:
    """The shared cart, loaded from disk on first access and pruned every run.

    Pruning on every run rather than only on load is deliberate: a tab left open
    across 20:00 must not go on offering to export a screening that has already
    started. ``sources.loader._now_paris`` is the single Paris-anchored clock
    ``future_showtimes`` uses too, so "past" means the same thing here as it does
    everywhere else in the app.
    """
    if CART_SESSION_KEY not in st.session_state:
        st.session_state[CART_SESSION_KEY] = load_cart()
    cart = cast(ScreeningCart, st.session_state[CART_SESSION_KEY])
    if prune_past(cart, _now_paris()):
        save_cart(cart)
    return cart


def _drop_pick_widgets() -> None:
    """Forget every mounted pills selection.

    Emptying the cart is not enough: the pills are still live, their selections
    still sit in session state, and the next run's ``reconcile_group`` would put
    every item straight back — the same failure "Clear pins" hit in ``chat.ui``,
    which pops ``pin_picker`` for exactly this reason.

    Swept by prefix rather than by reconstructing each key, because
    ``core.cart.pick_group_key`` hashes the option universe: a reconstructed key
    that drifts by one character silently resurrects the item, whereas a prefix
    sweep cannot miss.
    """
    for key in [k for k in st.session_state if isinstance(k, str) and k.startswith(PICK_KEY_PREFIX)]:
        st.session_state.pop(key, None)


def pick_labels(entry: AgendaEntry) -> dict[str, str]:
    """Pill label per showtime id: ``"19:00 · :gray[Le Champo]"``.

    **The venue is always shown, exactly as ``ui.agenda._time_pill_html`` shows it
    in browse mode.** An earlier version appended it only when the entry spanned
    two theaters, on the reasoning that the row above already said where — it does
    not: ``show_times=False`` drops the times block, and the row's meta line
    carries directors, runtime, rating and lens badge but never the theater. On the
    real programme 81% of entries are single-venue, so that rule hid the venue on
    four rows out of five and made plan mode strictly less informative than the
    mode it replaces. Plan and browse must show the same facts.

    ``:gray[…]`` is a Streamlit markdown directive, which ``st.pills`` renders in
    its labels; it reproduces the ``.time-pill-venue`` de-emphasis (time first and
    solid, venue secondary) without needing CSS that cannot reach inside a widget.

    Keyed by recomputing each showtime's id in place rather than zipping against
    :func:`~core.cart.entry_showtime_ids`: that list is de-duplicated, so a zip
    would slide out of step after the first duplicate and label a pill with a
    different screening's time.
    """
    film_key = str(entry.row.get("_film_key") or "")
    labels: dict[str, str] = {}
    for show in entry.showtimes:
        sid = showtime_id(film_key, show.when, show.theater_id)
        when = pd.Timestamp(show.when).strftime("%H:%M")
        labels.setdefault(sid, f"{when} · :gray[{show.theater}]" if show.theater else when)
    return labels


def cart_badge_label(cart: ScreeningCart) -> str:
    """Label for the cart popover — ``"Plan"`` or ``"Plan · 3"``.

    Mirrors ``pages.paris._filters_badge``: the count rides in the label so the
    toolbar shows the size of the plan without opening it.
    """
    return f"Plan · {len(cart)}" if len(cart) else "Plan"


def render_plan_agenda(
    days: list[AgendaDay],
    *,
    profile: TasteProfile | None = None,
    cart: ScreeningCart,
    index: dict[str, CartItem],
) -> bool:
    """Render the agenda with selectable showtimes; return whether the cart changed.

    Structurally different from ``ui.agenda.render_agenda`` in exactly one way,
    and it costs exactly one thing: the day header is emitted without its rows, so
    ``.agenda-day-head`` has no tall containing block to stick inside.
    ``.agenda-day--plan`` unsticks it explicitly rather than leaving a declaration
    that silently does nothing.

    Nothing else is forked — the row HTML is ``ui.agenda._agenda_row_html`` with
    ``show_times=False``, so poster, title link, lens badge and match chips are
    byte-identical to browse mode.

    The stretched ``.movie-card-link::after`` overlay is a non-issue here, and the
    column split is why: the overlay's containing block is
    ``.agenda-row--linked``, which lives entirely inside the left column's
    subtree, so it cannot reach a widget in a sibling column. No ``z-index`` lift
    is needed (unlike ``.chip--trailer`` inside a card) and the row still holds
    exactly one anchor.

    Returns the OR of every group's reconciliation, so the caller saves once per
    run rather than once per film.
    """
    changed = False
    for day in days:
        st.markdown(
            f'<section class="agenda-day agenda-day--plan">{_agenda_day_head_html(day)}</section>',
            unsafe_allow_html=True,
        )
        for entry in day.entries:
            universe = entry_showtime_ids(entry)
            labels = pick_labels(entry)
            suffix = pick_group_key(universe)

            col_row, col_pick = st.columns(_PLAN_COLUMNS, vertical_alignment="center")
            col_row.markdown(
                f'<div class="plan-row">{_agenda_row_html(entry, profile, show_times=False)}</div>',
                unsafe_allow_html=True,
            )
            with col_pick, st.container(key=f"{PICK_CONTAINER_PREFIX}{suffix}"):
                selection = st.pills(
                    f"Showtimes for {_title_of(entry.row)}",
                    options=universe,
                    selection_mode="multi",
                    # Reseeded from the cart on every fresh mount. Never paired
                    # with a session_state assignment on the same key: Streamlit
                    # warns about that, and filterwarnings=["error"] makes the
                    # warning a test failure.
                    default=[sid for sid in universe if sid in cart.items],
                    format_func=lambda sid: labels.get(sid, sid),
                    key=f"{PICK_KEY_PREFIX}{suffix}",
                    label_visibility="collapsed",
                )
            # st.pills is typed `list[V] | V | None` for the single/multi split;
            # multi always yields a list, and the comprehension narrows it.
            picked = [sid for sid in (selection or []) if isinstance(sid, str)]
            changed |= reconcile_group(cart, universe, picked, index)
    return changed


def _cart_row_html(item: CartItem) -> str:
    """One line of the cart panel: when, what, where — linked when we have a slug."""
    when = html.escape(pd.Timestamp(item.when).strftime("%a %d %b · %H:%M"))
    title = html.escape(item.title)
    slug = item.fields.get("letterboxd_slug")
    title_html = (
        f'<a class="movie-card-link" href="{movie_href(str(slug))}" target="_self">{title}</a>'
        if isinstance(slug, str) and slug
        else title
    )
    venue = f'<span class="plan-item-venue">{html.escape(item.theater)}</span>' if item.theater else ""
    return (
        f'<div class="plan-item">'
        f'<span class="plan-item-when">{when}</span>'
        f'<span class="plan-item-title">{title_html}</span>'
        f"{venue}"
        f"</div>"
    )


def render_cart_panel(cart: ScreeningCart) -> None:
    """The toolbar's plan popover: contents, the ``.ics`` download, remove and clear.

    Shows the **whole** cart, never the frame on screen. That is the inversion of
    the calendar page's "the export mirrors its filters" rule and it is the point
    of a cart: screenings picked under one lens, day or filter stay in the plan
    when the view moves on (see ``core.cart``'s module docstring).

    The export goes through ``ui.ics.build_ics_events`` + ``to_ics``, the same
    helpers the calendar and movie-detail pages use, so calendar-block sizing
    cannot drift between the three downloads.
    """
    with st.popover(cart_badge_label(cart), icon=":material/event_available:", use_container_width=True):
        items = cart.sorted_items()
        if not items:
            st.caption("Nothing planned yet. Turn on **Plan** and tap a showtime to add it here.")
            return

        st.markdown("".join(_cart_row_html(item) for item in items), unsafe_allow_html=True)

        st.download_button(
            "📅 .ics (Google / Apple / Outlook)",
            data=to_ics(build_ics_events(cart_frame(cart))),
            file_name="paris_plan.ics",
            mime="text/calendar",
            use_container_width=True,
            key="paris_cart_ics",
        )

        removed = st.selectbox(
            "Remove a screening",
            options=[None, *(item.id for item in items)],
            format_func=lambda sid: (
                "Remove a screening…" if sid is None else f"✕ {cart.items[sid].title} · {cart.items[sid].when:%a %H:%M}"
            ),
            key="paris_cart_remove",
            label_visibility="collapsed",
        )
        if removed is not None:
            cart.items.pop(removed, None)
            # Both of these matter: the pills for that film are still mounted and
            # would re-add it on the next run, and the selectbox would keep
            # pointing at an id that no longer exists.
            _drop_pick_widgets()
            st.session_state.pop("paris_cart_remove", None)
            save_cart(cart)
            st.rerun()

        if st.button("🗑 Clear plan", key="paris_cart_clear", use_container_width=True):
            cart.items.clear()
            _drop_pick_widgets()
            save_cart(cart)
            st.rerun()
