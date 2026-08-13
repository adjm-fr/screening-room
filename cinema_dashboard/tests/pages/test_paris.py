"""Tests for pages.paris — the page shell and its wiring.

The lens vocabulary these used to cover moved to ``core.lenses``; see
``tests/core/test_lenses.py``. What is left here is what only a page can be
asked: that it exposes ``main``, that it wires the *shared* helpers rather than
forking them, and that every widget key is ``paris_*``-namespaced.

Same import strategy as ``tests/pages/test_database.py``: ``pages/paris.py``
calls ``main()`` unconditionally at module import time (the Streamlit
multipage convention), so ``movies_output_path`` is patched to ``None``
*before* the first import — ``main()`` then hits its "configure your data
paths" early return and does no further work.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def _import_paris_page(module_mocker):
    module_mocker.patch("config.settings.movies_output_path", None)
    import pages.paris  # noqa: F401  (import side effect: registers the module in sys.modules)


# ── page shell ───────────────────────────────────────────────────────────────


def test_page_exposes_main_after_the_early_return():
    import pages.paris

    assert callable(pages.paris.main)


def test_page_uses_the_shared_filter_chain():
    """Guards the "one filter chain, one frame" invariant mechanically.

    The page shares the Watchlist Screenings machinery rather than re-deriving a
    frame of its own, so as long as it calls *these* functions the lens strip,
    the day strip and the agenda are all narrowings of the same chain.
    """
    import core.agenda
    import pages.paris

    assert pages.paris.apply_filters is core.agenda.apply_filters
    assert pages.paris.apply_day is core.agenda.apply_day
    assert pages.paris.build_agenda is core.agenda.build_agenda
    assert pages.paris.day_chips is core.agenda.day_chips


def test_page_uses_the_shared_lens_vocabulary():
    """The lens logic is Streamlit-free and lives in core; the page only renders it.

    Same mechanical guard as the filter chain below: as long as the page calls
    *these* functions, the chip counts, the ``_category`` column the agenda badges
    read, and the seen-film drop cannot drift apart from one another.
    """
    import core.lenses
    import pages.paris

    assert pages.paris.categorize is core.lenses.categorize
    assert pages.paris.drop_uninteresting_seen is core.lenses.drop_uninteresting_seen
    assert pages.paris.lens_counts is core.lenses.lens_counts
    assert pages.paris.LENS_LABELS is core.lenses.LENS_LABELS


def test_page_uses_the_shared_cart_helpers():
    """Same guard for the cart: the page wires the shared code, it does not fork it."""
    import core.cart
    import pages.paris
    import ui.cart

    assert pages.paris.cart_index is core.cart.cart_index
    assert pages.paris.save_cart is core.cart.save_cart
    assert pages.paris.cart_state is ui.cart.cart_state
    assert pages.paris.render_plan_agenda is ui.cart.render_plan_agenda
    assert pages.paris.render_cart_panel is ui.cart.render_cart_panel


def test_cart_keys_are_paris_namespaced():
    """A ``cal_*`` collision would make one page's plan mode follow the user onto the other."""
    import ui.cart

    assert ui.cart.CART_SESSION_KEY.startswith("paris_")
    assert ui.cart.PICK_KEY_PREFIX.startswith("paris_")
    # The cart itself must survive the pick-widget sweep on "Clear plan".
    assert not ui.cart.CART_SESSION_KEY.startswith(ui.cart.PICK_KEY_PREFIX)


def test_filters_badge_is_plain_when_nothing_is_set(mocker):
    import pages.paris

    mocker.patch.object(pages.paris.st, "session_state", {})
    assert pages.paris._filters_badge() == "Filters"


def test_filters_badge_counts_the_paris_namespaced_keys(mocker):
    """The keys are ``paris_*``: sharing ``cal_*`` would leak one page's filters onto the other."""
    import pages.paris

    mocker.patch.object(
        pages.paris.st,
        "session_state",
        {"paris_search": "kurosawa", "paris_theaters": ["Le Champo"], "paris_minrating": 3.5, "cal_tod": ["Evening"]},
    )
    assert pages.paris._filters_badge() == "Filters · 3"
