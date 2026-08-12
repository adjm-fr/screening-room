"""Tests for core.cart — showtime identity, snapshots, reconciliation, persistence.

Streamlit-free, like the module: nothing here needs an app context. The
widget-facing half lives in ``tests/ui/test_cart.py``.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

from core.agenda import build_agenda
from core.cart import (
    CART_SNAPSHOT_FIELDS,
    CartItem,
    ScreeningCart,
    cart_frame,
    cart_index,
    delete_cart,
    entry_showtime_ids,
    load_cart,
    pick_group_key,
    prune_past,
    reconcile_group,
    save_cart,
    showtime_id,
    snapshot,
)
from ui.ics import build_ics_events, screening_end

WHEN = pd.Timestamp("2026-08-04 19:00")


def _screening(**overrides) -> dict:
    row = {
        "showtimes": WHEN,
        "letterboxd_slug": "vertigo",
        "letterboxd_title": "Vertigo",
        "french_title": "Sueurs froides",
        "theater_id": "C0071",
        "theater_name": "Le Champo",
        "directors": "Alfred Hitchcock",
        "runtime_minutes": 128.0,
    }
    row.update(overrides)
    return row


def _frame(rows: list[dict] | None = None) -> pd.DataFrame:
    return pd.DataFrame(rows or [_screening()])


def _item(sid: str = "abc", *, when: pd.Timestamp = WHEN, **fields) -> CartItem:
    return CartItem(id=sid, film_key="vertigo", when=when, fields={**_screening(), **fields})


# ── showtime_id ──────────────────────────────────────────────────────────────


def test_showtime_id_is_stable_across_calls():
    assert showtime_id("vertigo", WHEN, "C0071") == showtime_id("vertigo", WHEN, "C0071")


def test_showtime_id_is_hex_only_so_it_is_safe_in_an_unescaped_ics_uid():
    """``to_ics`` writes ``UID:`` unescaped, and ``_film_key`` can fall back to a title."""
    sid = showtime_id("Drive, My Car; a film\nwith Amélie", WHEN, "C0071")
    assert len(sid) == 16
    assert all(c in "0123456789abcdef" for c in sid)


def test_showtime_id_differs_by_film_by_time_and_by_theater():
    base = showtime_id("vertigo", WHEN, "C0071")
    assert showtime_id("psycho", WHEN, "C0071") != base
    assert showtime_id("vertigo", WHEN + pd.Timedelta(minutes=15), "C0071") != base
    assert showtime_id("vertigo", WHEN, "C0999") != base


def test_showtime_id_ignores_sub_minute_jitter():
    """The pill renders %H:%M — two screenings a user cannot tell apart are one item."""
    assert showtime_id("vertigo", WHEN + pd.Timedelta(seconds=42), "C0071") == showtime_id("vertigo", WHEN, "C0071")


def test_showtime_id_delimiter_cannot_be_forged():
    """A title containing the unit separator must not collide with another triple."""
    assert showtime_id("a\x1fb", WHEN, "") != showtime_id("a", WHEN, "b")


# ── pick_group_key ───────────────────────────────────────────────────────────


def test_pick_group_key_is_stable_for_the_same_universe():
    assert pick_group_key(["a", "b"]) == pick_group_key(["a", "b"])


def test_pick_group_key_changes_when_an_option_is_added_or_removed():
    """This is the reseed mechanism: a changed universe must mount a NEW widget.

    With a fixed key, Streamlit would ignore ``default=`` (the key already
    exists) and report its own pruned selection, so reconciliation would delete
    a pick the user never touched.
    """
    base = pick_group_key(["a", "b"])
    assert pick_group_key(["a", "b", "c"]) != base
    assert pick_group_key(["a"]) != base


def test_pick_group_key_is_widget_and_css_safe():
    """It lands in a widget key and in ``st.container(key=…)`` → ``.st-key-…``."""
    key = pick_group_key(["Drive, My Car", "a b\nc"])
    assert all(c in "0123456789abcdef" for c in key)


# ── snapshot / cart_index ────────────────────────────────────────────────────


def test_snapshot_fields_cover_everything_the_ics_builders_read():
    """The contract test: a cart item must export with no help from the source frame.

    Extend this alongside ``CART_SNAPSHOT_FIELDS`` — it is what replaces the
    pinned-recs "re-resolve the row at render time" rule.
    """
    fields = snapshot(pd.Series(_screening()))
    frame = pd.DataFrame([fields], index=["deadbeefdeadbeef"])

    (event,) = build_ics_events(frame)
    assert event["summary"] == "Vertigo"
    assert event["location"] == "Le Champo"
    assert "Alfred Hitchcock" in event["description"]
    assert event["start"] == WHEN
    assert event["end"] == screening_end(frame.iloc[0], WHEN)
    assert event["uid"].startswith("deadbeefdeadbeef-")


def test_snapshot_coerces_nan_to_none_so_the_summary_never_reads_nan():
    """``json.dump(default=str)`` renders NaN as the truthy string "nan"."""
    fields = snapshot(pd.Series(_screening(letterboxd_title=float("nan"))))
    assert fields["letterboxd_title"] is None

    (event,) = build_ics_events(pd.DataFrame([fields]))
    assert event["summary"] == "Sueurs froides"


def test_snapshot_keeps_only_the_declared_fields():
    fields = snapshot(pd.Series(_screening(_film_key="vertigo", match=91.0)))
    assert set(fields) == set(CART_SNAPSHOT_FIELDS)


def test_cart_index_keys_match_entry_showtime_ids():
    """Anti-drift: the renderer's ids and the frame's index must be the same ids.

    ``entry_showtime_ids`` reads an ``AgendaEntry``; ``cart_index`` reads the
    frame. If they ever disagree, a tick would look up nothing and silently fail
    to add.
    """
    frame = _frame([_screening(), _screening(showtimes=pd.Timestamp("2026-08-04 21:30"))])
    index = cart_index(frame)
    for day in build_agenda(frame):
        for entry in day.entries:
            assert set(entry_showtime_ids(entry)) <= set(index)


def test_cart_index_applies_agenda_columns_to_a_raw_frame():
    """A caller may pass a pre-filter frame; ``_dt``/``_film_key`` are derived here."""
    assert len(cart_index(_frame())) == 1


def test_cart_index_dedupes_identical_screenings():
    """Allocine emits one row per language version and the contract has no version column."""
    assert len(cart_index(_frame([_screening(), _screening()]))) == 1


def test_cart_index_of_an_empty_frame_is_empty():
    assert cart_index(pd.DataFrame({"showtimes": []})) == {}


# ── reconcile_group ──────────────────────────────────────────────────────────


def test_reconcile_adds_selected_and_removes_deselected():
    index = cart_index(_frame())
    (sid,) = index
    cart = ScreeningCart()

    assert reconcile_group(cart, [sid], [sid], index) is True
    assert set(cart.items) == {sid}
    assert reconcile_group(cart, [sid], [], index) is True
    assert cart.items == {}


def test_reconcile_leaves_ids_outside_the_universe_untouched():
    """The lens/day/filter guarantee: an unrendered group keeps its items."""
    kept = _item("elsewhere")
    cart = ScreeningCart(items={"elsewhere": kept})
    index = cart_index(_frame())
    (sid,) = index

    reconcile_group(cart, [sid], [sid], index)
    assert cart.items["elsewhere"] is kept


def test_reconcile_reports_no_change_when_the_selection_already_matches():
    index = cart_index(_frame())
    (sid,) = index
    cart = ScreeningCart(items={sid: index[sid]})
    assert reconcile_group(cart, [sid], [sid], index) is False


def test_reconcile_ignores_a_selected_id_missing_from_the_index():
    cart = ScreeningCart()
    assert reconcile_group(cart, ["ghost"], ["ghost"], {}) is False
    assert cart.items == {}


# ── cart_frame and UID stability ─────────────────────────────────────────────


def test_cart_frame_is_indexed_by_showtime_id():
    cart = ScreeningCart(items={"cafe1234cafe1234": _item("cafe1234cafe1234")})
    assert list(cart_frame(cart).index) == ["cafe1234cafe1234"]
    assert list(cart_frame(cart).columns) == list(CART_SNAPSHOT_FIELDS)


def test_cart_frame_is_chronological_not_pick_order():
    late = _item("late", when=pd.Timestamp("2026-08-04 22:00"), showtimes=pd.Timestamp("2026-08-04 22:00"))
    early = _item("early", when=WHEN)
    cart = ScreeningCart(items={"late": late, "early": early})
    assert list(cart_frame(cart).index) == ["early", "late"]


def test_uids_are_stable_across_two_exports_of_the_same_cart():
    cart = ScreeningCart(items={"cafe1234cafe1234": _item("cafe1234cafe1234")})
    first = [e["uid"] for e in build_ics_events(cart_frame(cart))]
    second = [e["uid"] for e in build_ics_events(cart_frame(cart))]
    assert first == second


def test_uids_of_two_different_carts_never_collide():
    """The bug this whole index scheme exists for.

    Under a fresh ``RangeIndex`` both exports would emit ``UID:0-…`` for
    different films and a calendar app would overwrite the first import.
    """
    one = ScreeningCart(items={"aaaa1111aaaa1111": _item("aaaa1111aaaa1111")})
    two = ScreeningCart(items={"bbbb2222bbbb2222": _item("bbbb2222bbbb2222", letterboxd_title="Psycho")})
    uids_one = {e["uid"] for e in build_ics_events(cart_frame(one))}
    uids_two = {e["uid"] for e in build_ics_events(cart_frame(two))}
    assert uids_one.isdisjoint(uids_two)


def test_cart_frame_of_an_empty_cart_exports_a_valid_empty_calendar():
    from ui.ics import to_ics

    out = to_ics(build_ics_events(cart_frame(ScreeningCart()))).decode("utf-8")
    assert out.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" not in out


def test_export_works_off_a_cart_reloaded_from_disk(tmp_path):
    """``default=str`` stringifies Timestamps; the export path must re-parse them."""
    path = tmp_path / "paris_cart.json"
    index = cart_index(_frame())
    save_cart(ScreeningCart(items=dict(index)), path)

    (event,) = build_ics_events(cart_frame(load_cart(path)))
    assert event["summary"] == "Vertigo"
    assert pd.to_datetime(event["start"]) == WHEN
    assert pd.to_datetime(event["end"]) == WHEN + pd.Timedelta(minutes=10 + 128)


# ── prune_past ───────────────────────────────────────────────────────────────


def test_prune_past_drops_started_screenings_and_keeps_future_ones():
    cart = ScreeningCart(
        items={
            "gone": _item("gone", when=pd.Timestamp("2026-08-04 18:00")),
            "soon": _item("soon", when=pd.Timestamp("2026-08-04 20:00")),
        }
    )
    assert prune_past(cart, pd.Timestamp("2026-08-04 19:00")) == 1
    assert set(cart.items) == {"soon"}


def test_prune_past_accepts_a_tz_aware_now_against_naive_wall_clock_items():
    """``sources.loader._now_paris`` is tz-aware; every showtime here is naive Paris."""
    cart = ScreeningCart(items={"soon": _item("soon", when=pd.Timestamp("2026-08-04 20:00"))})
    assert prune_past(cart, pd.Timestamp("2026-08-04 19:00", tz="Europe/Paris")) == 0
    assert set(cart.items) == {"soon"}


def test_prune_past_on_an_empty_cart_is_a_no_op():
    assert prune_past(ScreeningCart(), pd.Timestamp("2026-08-04 19:00")) == 0


# ── Persistence ──────────────────────────────────────────────────────────────


def test_cart_round_trip(tmp_path):
    path = tmp_path / "paris_cart.json"
    cart = ScreeningCart(items=dict(cart_index(_frame())))
    save_cart(cart, path)

    loaded = load_cart(path)
    assert set(loaded.items) == set(cart.items)
    item = next(iter(loaded.items.values()))
    assert item.when == WHEN
    assert item.fields["letterboxd_title"] == "Vertigo"


def test_save_cart_creates_the_data_directory(tmp_path):
    path = tmp_path / "nested" / "paris_cart.json"
    save_cart(ScreeningCart(), path)
    assert path.exists()


def test_load_cart_absent_file_returns_an_empty_cart(tmp_path):
    assert load_cart(tmp_path / "missing.json") == ScreeningCart()


def test_load_cart_corrupt_file_returns_an_empty_cart(tmp_path, caplog):
    path = tmp_path / "paris_cart.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.cart"):
        assert load_cart(path) == ScreeningCart()
    assert "unreadable cart" in caplog.text


@pytest.mark.parametrize("payload", ["[]", '{"items": "not a list"}'])
def test_load_cart_wrong_shape_returns_an_empty_cart(tmp_path, payload):
    path = tmp_path / "paris_cart.json"
    path.write_text(payload, encoding="utf-8")
    assert load_cart(path) == ScreeningCart()


def test_load_cart_skips_one_unusable_item_and_keeps_the_rest(tmp_path, caplog):
    """A future schema change should cost one screening, not the whole plan."""
    path = tmp_path / "paris_cart.json"
    good = {"id": "aaaa1111aaaa1111", "film_key": "vertigo", "when": str(WHEN), "fields": {}}
    path.write_text(json.dumps({"items": [good, {"id": "no-when"}, "junk"]}), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="core.cart"):
        loaded = load_cart(path)
    assert set(loaded.items) == {"aaaa1111aaaa1111"}
    assert "unusable cart item" in caplog.text


def test_delete_cart_removes_the_file_and_tolerates_a_missing_one(tmp_path):
    path = tmp_path / "paris_cart.json"
    save_cart(ScreeningCart(), path)
    assert path.exists()
    delete_cart(path)
    assert not path.exists()
    delete_cart(path)  # second delete must not raise
