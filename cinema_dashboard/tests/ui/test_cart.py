"""Tests for ui.cart — plan-mode rendering, pill labels, and the widget-key sweep.

Streamlit is patched per call (the ``tests/ui/test_agenda.py`` pattern); nothing
here needs an app context. The pure cart logic lives in ``tests/core/test_cart.py``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from core.agenda import AgendaDay, AgendaEntry, AgendaShowtime
from core.cart import CartItem, ScreeningCart, cart_index, showtime_id
from ui.cart import (
    CART_SESSION_KEY,
    PICK_CONTAINER_PREFIX,
    PICK_KEY_PREFIX,
    _drop_pick_widgets,
    cart_badge_label,
    pick_labels,
    render_plan_agenda,
)

WHEN = pd.Timestamp("2026-08-04 19:00")


def _entry(*, showtimes: list[tuple[str, str, str]] | None = None, **overrides) -> AgendaEntry:
    data = {
        "_film_key": "vertigo",
        "letterboxd_slug": "vertigo",
        "letterboxd_title": "Vertigo",
        "directors": "Alfred Hitchcock",
        "runtime_minutes": 128.0,
    }
    data.update(overrides)
    triples = showtimes or [("2026-08-04 19:00", "Le Champo", "C0071")]
    stamps = tuple(AgendaShowtime(when=pd.Timestamp(when), theater=theater, theater_id=tid) for when, theater, tid in triples)
    return AgendaEntry(row=pd.Series(data), showtimes=stamps, earliest=stamps[0].when, match=None)


def _day(entries: list[AgendaEntry]) -> AgendaDay:
    return AgendaDay(day=dt.date(2026, 8, 4), label="Tonight", is_today=True, entries=tuple(entries))


def _day_list(n: int) -> list[AgendaDay]:
    return [_day([_entry(letterboxd_title=f"Film {i}", _film_key=f"film-{i}") for i in range(n)])]


def _patch_streamlit(mocker, *, selection: list[str] | None = None):
    """Patch the four Streamlit calls ``render_plan_agenda`` makes; return the mocks."""
    markdown = mocker.patch("ui.cart.st.markdown")
    column = mocker.MagicMock()
    columns = mocker.patch("ui.cart.st.columns", return_value=(column, column))
    container = mocker.patch("ui.cart.st.container")
    pills = mocker.patch("ui.cart.st.pills", return_value=selection or [])
    return markdown, columns, container, pills, column


# ── pick_labels ──────────────────────────────────────────────────────────────


def test_pick_labels_always_name_the_venue_even_within_one_theater():
    """Regression: 81% of real entries are single-venue, and plan mode drops the
    times block, so a "only when ambiguous" rule hid the theater on most rows and
    made plan mode less informative than browse mode."""
    labels = pick_labels(_entry(showtimes=[("2026-08-04 19:00", "Le Champo", "C1"), ("2026-08-04 21:30", "Le Champo", "C1")]))
    assert sorted(labels.values()) == ["19:00 · :gray[Le Champo]", "21:30 · :gray[Le Champo]"]


def test_pick_labels_distinguish_two_theaters_at_the_same_time():
    """Two pills reading "19:00" would be indistinguishable."""
    labels = pick_labels(_entry(showtimes=[("2026-08-04 19:00", "Le Champo", "C1"), ("2026-08-04 19:00", "mk2 Odéon", "C2")]))
    assert sorted(labels.values()) == ["19:00 · :gray[Le Champo]", "19:00 · :gray[mk2 Odéon]"]


def test_pick_labels_fall_back_to_the_time_when_the_theater_is_unknown():
    labels = pick_labels(_entry(showtimes=[("2026-08-04 19:00", "", "C1")]))
    assert list(labels.values()) == ["19:00"]


def test_pick_labels_key_each_showtime_to_its_own_time():
    """Regression: zipping deduped ids against undeduped showtimes mislabels pills."""
    entry = _entry(
        showtimes=[
            ("2026-08-04 19:00", "Le Champo", "C1"),
            ("2026-08-04 19:00", "Le Champo", "C1"),  # duplicate — one id
            ("2026-08-04 21:30", "Le Champo", "C1"),
        ]
    )
    labels = pick_labels(entry)
    assert labels[showtime_id("vertigo", pd.Timestamp("2026-08-04 21:30"), "C1")] == "21:30 · :gray[Le Champo]"


# ── cart_badge_label ─────────────────────────────────────────────────────────


def test_cart_badge_label_is_plain_when_the_cart_is_empty():
    assert cart_badge_label(ScreeningCart()) == "Plan"


def test_cart_badge_label_counts_the_cart():
    cart = ScreeningCart(items={"a": CartItem(id="a", film_key="v", when=WHEN, fields={})})
    assert cart_badge_label(cart) == "Plan · 1"


# ── render_plan_agenda ───────────────────────────────────────────────────────


def test_plan_row_omits_the_static_time_pills():
    """Plan mode replaces them with a real widget; rendering both would duplicate."""
    from ui.agenda import _agenda_row_html

    assert "time-pill" not in _agenda_row_html(_entry(), show_times=False)
    assert "time-pill" in _agenda_row_html(_entry())


def test_plan_agenda_mounts_one_pills_widget_per_entry(mocker):
    _, _, _, pills, _ = _patch_streamlit(mocker)
    render_plan_agenda(_day_list(2), cart=ScreeningCart(), index={})
    assert pills.call_count == 2


def test_plan_agenda_emits_the_day_header_without_its_rows(mocker):
    """The deliberate trade: a widget cannot live in the blob, so sticky is given up."""
    markdown, _, _, _, column = _patch_streamlit(mocker)
    render_plan_agenda(_day_list(2), cart=ScreeningCart(), index={})

    (header,) = [call.args[0] for call in markdown.call_args_list]
    assert "agenda-day--plan" in header
    assert "agenda-day-head" in header
    assert "agenda-row" not in header
    # The rows go into the left column instead, one markdown call each.
    assert column.markdown.call_count == 2


def test_plan_agenda_widget_keys_are_paris_namespaced(mocker):
    """Both pages can live in one session, so a shared key would leak across them."""
    _, _, _, pills, _ = _patch_streamlit(mocker)
    render_plan_agenda(_day_list(2), cart=ScreeningCart(), index={})
    keys = [call.kwargs["key"] for call in pills.call_args_list]
    assert keys and all(key.startswith("paris_") for key in keys)


def test_plan_agenda_wraps_each_group_in_a_cartpick_container(mocker):
    """The CSS that lets a six-showtime group wrap selects on this container."""
    _, _, container, _, _ = _patch_streamlit(mocker)
    render_plan_agenda(_day_list(1), cart=ScreeningCart(), index={})
    (call,) = container.call_args_list
    assert call.kwargs["key"].startswith(PICK_CONTAINER_PREFIX)


def test_plan_agenda_seeds_the_pills_from_the_cart(mocker):
    """The cart is the source of truth; the widget is a view of it."""
    entry = _entry()
    sid = showtime_id("vertigo", WHEN, "C0071")
    cart = ScreeningCart(items={sid: CartItem(id=sid, film_key="vertigo", when=WHEN, fields={})})

    _, _, _, pills, _ = _patch_streamlit(mocker)
    render_plan_agenda([_day([entry])], cart=cart, index={})
    assert pills.call_args.kwargs["default"] == [sid]


def test_plan_agenda_ticking_a_pill_adds_it_to_the_cart(mocker):
    frame = pd.DataFrame([{"showtimes": WHEN, "letterboxd_slug": "vertigo", "theater_id": "C0071"}])
    index = cart_index(frame)
    (sid,) = index

    _patch_streamlit(mocker, selection=[sid])
    cart = ScreeningCart()
    assert render_plan_agenda([_day([_entry()])], cart=cart, index=index) is True
    assert set(cart.items) == {sid}


def test_plan_agenda_reports_no_change_when_nothing_is_picked(mocker):
    _patch_streamlit(mocker)
    assert render_plan_agenda(_day_list(1), cart=ScreeningCart(), index={}) is False


def test_plan_agenda_of_nothing_renders_nothing(mocker):
    markdown, _, _, pills, _ = _patch_streamlit(mocker)
    assert render_plan_agenda([], cart=ScreeningCart(), index={}) is False
    markdown.assert_not_called()
    pills.assert_not_called()


# ── _drop_pick_widgets ───────────────────────────────────────────────────────


def test_drop_pick_widgets_pops_only_the_pick_keys(mocker):
    """Clearing the cart without this puts every item straight back next run."""
    session = {
        f"{PICK_KEY_PREFIX}abc": ["x"],
        f"{PICK_KEY_PREFIX}def": [],
        CART_SESSION_KEY: ScreeningCart(),
        "paris_day": "2026-08-04",
        "cal_search": "kurosawa",
    }
    import ui.cart

    mocker.patch.object(ui.cart.st, "session_state", session)
    _drop_pick_widgets()
    assert set(session) == {CART_SESSION_KEY, "paris_day", "cal_search"}


def test_cart_session_key_is_not_swept_by_the_pick_prefix():
    """A one-character slip here would wipe the cart on every clear."""
    assert not CART_SESSION_KEY.startswith(PICK_KEY_PREFIX)
    assert CART_SESSION_KEY.startswith("paris_")
    assert PICK_KEY_PREFIX.startswith("paris_")


# ── cart_state ───────────────────────────────────────────────────────────────


def test_cart_state_loads_from_disk_on_first_access_then_reuses_the_session(mocker):
    import ui.cart

    session: dict = {}
    mocker.patch.object(ui.cart.st, "session_state", session)
    mocker.patch.object(ui.cart, "_now_paris", return_value=pd.Timestamp("2026-08-04 12:00", tz="Europe/Paris"))
    load = mocker.patch.object(ui.cart, "load_cart", return_value=ScreeningCart())

    first = ui.cart.cart_state()
    second = ui.cart.cart_state()
    assert first is second
    assert load.call_count == 1
    assert session[CART_SESSION_KEY] is first


def test_cart_state_prunes_started_screenings_every_run_and_persists(mocker):
    """A tab open across 20:00 must stop offering a screening that already began."""
    import ui.cart

    gone = CartItem(id="gone", film_key="v", when=pd.Timestamp("2026-08-04 18:00"), fields={})
    cart = ScreeningCart(items={"gone": gone})
    mocker.patch.object(ui.cart.st, "session_state", {CART_SESSION_KEY: cart})
    mocker.patch.object(ui.cart, "_now_paris", return_value=pd.Timestamp("2026-08-04 19:00", tz="Europe/Paris"))
    save = mocker.patch.object(ui.cart, "save_cart")

    assert ui.cart.cart_state().items == {}
    save.assert_called_once_with(cart)


# ── render_cart_panel ────────────────────────────────────────────────────────


def _patch_panel(mocker, *, removed=None, cleared=False):
    import ui.cart

    mocker.patch.object(ui.cart.st, "popover", return_value=mocker.MagicMock())
    mocker.patch.object(ui.cart.st, "markdown")
    caption = mocker.patch.object(ui.cart.st, "caption")
    download = mocker.patch.object(ui.cart.st, "download_button")
    mocker.patch.object(ui.cart.st, "selectbox", return_value=removed)
    mocker.patch.object(ui.cart.st, "button", return_value=cleared)
    mocker.patch.object(ui.cart.st, "session_state", {})
    rerun = mocker.patch.object(ui.cart.st, "rerun")
    save = mocker.patch.object(ui.cart, "save_cart")
    return caption, download, rerun, save


def test_cart_panel_of_an_empty_cart_offers_no_download(mocker):
    import ui.cart

    caption, download, _, _ = _patch_panel(mocker)
    ui.cart.render_cart_panel(ScreeningCart())
    caption.assert_called_once()
    download.assert_not_called()


def test_cart_panel_downloads_an_ics_of_the_whole_cart(mocker):
    """The whole cart, never the frame on screen — that is the inversion."""
    import ui.cart

    item = CartItem(id="aaaa1111aaaa1111", film_key="v", when=WHEN, fields={"showtimes": WHEN, "letterboxd_title": "Vertigo"})
    _, download, _, _ = _patch_panel(mocker)
    ui.cart.render_cart_panel(ScreeningCart(items={item.id: item}))

    payload = download.call_args.kwargs["data"].decode("utf-8")
    assert "SUMMARY:Vertigo" in payload
    assert download.call_args.kwargs["file_name"] == "paris_plan.ics"
    assert download.call_args.kwargs["mime"] == "text/calendar"


def test_cart_panel_clear_drops_the_pick_widgets_too(mocker):
    """Emptying the cart alone would let the still-mounted pills re-add everything."""
    import ui.cart

    item = CartItem(id="aaaa1111aaaa1111", film_key="v", when=WHEN, fields={"showtimes": WHEN})
    cart = ScreeningCart(items={item.id: item})
    _, _, rerun, save = _patch_panel(mocker, cleared=True)
    ui.cart.st.session_state[f"{PICK_KEY_PREFIX}abc"] = [item.id]

    ui.cart.render_cart_panel(cart)
    assert cart.items == {}
    assert f"{PICK_KEY_PREFIX}abc" not in ui.cart.st.session_state
    save.assert_called_once_with(cart)
    rerun.assert_called_once()


def test_cart_panel_remove_drops_one_item_and_resets_the_picker(mocker):
    import ui.cart

    item = CartItem(id="aaaa1111aaaa1111", film_key="v", when=WHEN, fields={"showtimes": WHEN})
    cart = ScreeningCart(items={item.id: item})
    _, _, rerun, save = _patch_panel(mocker, removed=item.id)
    ui.cart.st.session_state[f"{PICK_KEY_PREFIX}abc"] = [item.id]

    ui.cart.render_cart_panel(cart)
    assert cart.items == {}
    assert f"{PICK_KEY_PREFIX}abc" not in ui.cart.st.session_state
    assert "paris_cart_remove" not in ui.cart.st.session_state
    save.assert_called_once_with(cart)
    rerun.assert_called_once()


# ── Shared-helper identity guards ────────────────────────────────────────────


def test_cart_export_uses_the_shared_ics_builders():
    """A forked builder is how the three .ics downloads would drift apart."""
    import ui.cart
    import ui.ics

    assert ui.cart.build_ics_events is ui.ics.build_ics_events
    assert ui.cart.to_ics is ui.ics.to_ics


def test_plan_renderer_uses_the_shared_row_renderer():
    """Plan mode must not fork the row HTML — that is what ``show_times`` is for."""
    import ui.agenda
    import ui.cart

    assert ui.cart._agenda_row_html is ui.agenda._agenda_row_html
    assert ui.cart._agenda_day_head_html is ui.agenda._agenda_day_head_html
