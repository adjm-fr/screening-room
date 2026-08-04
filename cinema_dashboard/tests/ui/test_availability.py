"""Tests for ui.availability — the shared "Only times I'm free" control.

The rule itself is covered by ``tests/core/test_availability.py``; these cover
the split this module exists for: a selection that renders in one place and is
applied in another, by two pages with different pipeline shapes.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from ui.availability import DEFAULT_CUTOFF, FreeTimeSelection, render_free_time_filter

# 2026-01-03 is a Saturday, 2026-01-05..07 Mon–Wed.
_ROWS = pd.DataFrame(
    {
        "showtimes": [
            "2026-01-03 14:00",  # weekend matinee — free
            "2026-01-05 14:00",  # Monday matinee — not free
            "2026-01-05 20:00",  # Monday evening — free (after cutoff)
            "2026-01-06 20:00",  # Tuesday evening — free (after cutoff)
        ]
    }
)


def test_disabled_selection_is_a_passthrough():
    out = FreeTimeSelection(enabled=False).apply(_ROWS)
    assert len(out) == len(_ROWS)


def test_disabled_selection_ignores_its_other_fields():
    # A stale cutoff/day list must not leak through once the toggle is off.
    sel = FreeTimeSelection(enabled=False, cutoff=dt.time(23, 59), unavailable=(dt.date(2026, 1, 3),))
    assert len(sel.apply(_ROWS)) == len(_ROWS)


def test_enabled_selection_drops_the_weekday_matinee():
    out = FreeTimeSelection(enabled=True).apply(_ROWS)
    assert "2026-01-05 14:00" not in set(out["showtimes"])
    assert len(out) == 3


def test_day_off_admits_that_days_matinee():
    out = FreeTimeSelection(enabled=True, days_off=(dt.date(2026, 1, 5),)).apply(_ROWS)
    assert "2026-01-05 14:00" in set(out["showtimes"])


def test_unavailable_overrides_the_weekend():
    out = FreeTimeSelection(enabled=True, unavailable=(dt.date(2026, 1, 3),)).apply(_ROWS)
    assert "2026-01-03 14:00" not in set(out["showtimes"])


def test_apply_on_an_empty_frame_is_a_no_op():
    # Guards the `rows.empty` short-circuit: an empty frame has no "showtimes"
    # column to mask, so this would raise without it.
    empty = pd.DataFrame()
    assert FreeTimeSelection(enabled=True).apply(empty).empty


def test_default_cutoff_is_the_documented_one():
    assert FreeTimeSelection(enabled=True).cutoff == DEFAULT_CUTOFF == dt.time(19, 0)


def test_render_returns_a_disabled_selection_when_the_toggle_is_off(mocker):
    # Toggle off means the pickers never render — no widget calls to read.
    mocker.patch("ui.availability.st.toggle", return_value=False)
    columns = mocker.patch("ui.availability.st.columns")

    sel = render_free_time_filter(_ROWS, key_prefix="test")

    assert sel.enabled is False
    columns.assert_not_called()


def test_render_namespaces_its_widget_keys_by_prefix(mocker):
    # Both pages can be in one session, so the four keys must not collide.
    toggle = mocker.patch("ui.availability.st.toggle", return_value=False)

    render_free_time_filter(_ROWS, key_prefix="paris")

    assert toggle.call_args.kwargs["key"] == "paris_free"


def test_render_collects_the_picker_values_when_enabled(mocker):
    mocker.patch("ui.availability.st.toggle", return_value=True)
    mocker.patch("ui.availability.st.columns", return_value=[mocker.MagicMock() for _ in range(3)])
    mocker.patch("ui.availability.st.time_input", return_value=dt.time(21, 0))
    mocker.patch("ui.availability.st.multiselect", side_effect=[[dt.date(2026, 1, 5)], [dt.date(2026, 1, 6)]])

    sel = render_free_time_filter(_ROWS, key_prefix="cal")

    assert sel.enabled is True
    assert sel.cutoff == dt.time(21, 0)
    assert sel.days_off == (dt.date(2026, 1, 5),)
    assert sel.unavailable == (dt.date(2026, 1, 6),)


def test_selection_is_frozen():
    # It's read once and applied later, possibly several times — mutating it
    # between those points would silently desync the two pages' frames.
    with pytest.raises(AttributeError):
        FreeTimeSelection(enabled=False).enabled = True  # type: ignore[misc]
