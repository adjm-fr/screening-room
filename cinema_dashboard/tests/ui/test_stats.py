"""Tests for ui.stats — the database page's CSS stat-bar builders."""

import html

import pandas as pd

from core.library import RATING_TIERS, rating_histogram
from ui.stats import BarRow, bar_rows_html, decade_profile_html, frequency_bars_html, rating_histogram_html


def _hist_df(counts: dict[float, int]) -> pd.DataFrame:
    base = rating_histogram(pd.DataFrame({"user_rating": []}))
    base["count"] = [counts.get(r, 0) for r in base["rating"]]
    return base


# ── bar_rows_html ───────────────────────────────────────────────────────────


def test_bar_rows_escape_texts_but_trust_style():
    out = bar_rows_html([BarRow(label="<b>", width_pct=50.0, color="hsl(36 80% 60%)", value_text="1 & 2", sublabel="<i>")])
    assert "&lt;b&gt;" in out
    assert "1 &amp; 2" in out
    assert "&lt;i&gt;" in out
    assert "width:50%;background:hsl(36 80% 60%)" in out


def test_bar_rows_omit_empty_sublabel():
    assert "hist-sub" not in bar_rows_html([BarRow(label="x", width_pct=0.0, color="c", value_text="0")])


# ── rating_histogram_html ───────────────────────────────────────────────────


def test_histogram_html_renders_all_bins_under_tier_headers():
    out = rating_histogram_html(_hist_df({3.0: 10, 5.0: 2}))
    assert out.count('class="hist-row"') == 10
    for _, _, label in RATING_TIERS:
        assert html.escape(label) in out
    assert out.count('class="hist-group"') == len(RATING_TIERS)


def test_histogram_html_orders_best_tier_first():
    out = rating_histogram_html(_hist_df({3.0: 1}))
    assert out.index("Masterpiece") < out.index(html.escape("Don't bother"))
    assert out.index("★ 5") < out.index("★ 0.5")


def test_histogram_html_zero_count_bin_renders_at_width_zero():
    out = rating_histogram_html(_hist_df({3.0: 10}))
    assert "width:0%" in out
    assert "width:100%" in out


def test_histogram_html_value_text_pairs_count_and_share():
    out = rating_histogram_html(_hist_df({3.0: 3, 4.0: 1}))
    assert "3 · 75%" in out
    assert "1 · 25%" in out


def test_histogram_html_empty_when_nothing_counted():
    assert rating_histogram_html(_hist_df({})) == ""
    assert rating_histogram_html(pd.DataFrame()) == ""


# ── decade_profile_html ─────────────────────────────────────────────────────


def test_decade_html_widths_and_value_text():
    df = pd.DataFrame({"decade": [1990, 2000], "count": [4, 2], "mean_rating": [3.25, float("nan")]})
    out = decade_profile_html(df)
    assert "1990s" in out and "2000s" in out
    assert "width:100%" in out and "width:50%" in out
    assert "4 · μ 3.2" in out
    assert "2 · μ —" in out


def test_decade_html_empty_frame_is_empty():
    assert decade_profile_html(pd.DataFrame({"decade": [], "count": [], "mean_rating": []})) == ""


# ── frequency_bars_html ──────────────────────────────────────────────────────


def test_frequency_bars_widths_and_values():
    df = pd.DataFrame({"bucket": ["<90", "90–120"], "count": [1, 4]})
    out = frequency_bars_html(df, label_col="bucket", count_col="count")
    assert "width:25%" in out
    assert "width:100%" in out
    assert ">1<" in out and ">4<" in out


def test_frequency_bars_use_flat_color_not_rating_ramp():
    df = pd.DataFrame({"genre": ["Drama"], "count": [3]})
    out = frequency_bars_html(df, label_col="genre", count_col="count")
    assert "hsl(" not in out
    assert "var(--primary-color)" in out


def test_frequency_bars_empty_or_all_zero_is_empty():
    assert frequency_bars_html(pd.DataFrame({"genre": [], "count": []}), label_col="genre", count_col="count") == ""
    assert frequency_bars_html(pd.DataFrame({"genre": ["A"], "count": [0]}), label_col="genre", count_col="count") == ""
