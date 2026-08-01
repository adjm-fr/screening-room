"""Tests for ui.chips — taste-match badge rendering (no Streamlit context required)."""

from __future__ import annotations

import pandas as pd

from core.taste import TasteProfile
from ui import match_chips_html

# ── match_chips_html ─────────────────────────────────────────────────────────


def _make_profile() -> TasteProfile:
    # counts mirror every affinity value (explain recovers mean ratings from
    # them): n=10 with mu=3.0 puts Comedy at a 1.65 mean — below the sentiment
    # pivot, so it can never surface as a chip.
    affinities = {"directors": {"Alfred Hitchcock": 0.9}, "genres": {"Western": 0.5, "Comedy": -0.9}}
    return TasteProfile(
        mu=3.0,
        n_ratings=10,
        affinities=affinities,
        counts={dim: {value: 10 for value in values} for dim, values in affinities.items()},
    )


def test_match_chips_html_contains_text_and_classes():
    row = pd.Series({"match": 72.4, "directors": "Alfred Hitchcock", "genres": "Western"})
    out = match_chips_html(row, _make_profile())
    assert 'class="chip chip--match"' in out
    assert "◎ 72% match" in out
    assert 'class="chip chip--why"' in out
    assert "✓ Alfred Hitchcock" in out


def test_match_chips_html_empty_when_no_match_value():
    profile = _make_profile()
    assert match_chips_html(pd.Series({"match": float("nan")}), profile) == ""
    assert match_chips_html(pd.Series({"title": "X"}), profile) == ""


def test_match_chips_html_badge_only_when_no_liked_contributors():
    row = pd.Series({"match": 31.0, "genres": "Comedy"})
    out = match_chips_html(row, _make_profile())
    assert "% match" in out
    assert "chip--why" not in out
