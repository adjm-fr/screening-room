"""
Dependency-free stat bars for the Movies Database page.

Pure ``-> str`` HTML builders over :mod:`core.library`'s frames, rendered by
the page through one ``st.markdown`` call each. CSS bars instead of Plotly on
purpose: the tier ladder grouping *is* the chart's point (the same
header-over-rows pattern as the movie detail page's ``.contrib-*`` taste
breakdown, which these ``.hist-*`` classes are cloned from), 10 fixed
half-star bins need no interactivity, and pure builders are unit-testable.

Color always rides with a numeric label (WCAG 1.4.1): every bar pairs its
``rating_to_hsl`` fill with the count/mean text in ``hist-value``.
"""

from __future__ import annotations

import dataclasses
import html
from collections.abc import Sequence

import pandas as pd

from core.library import RATING_TIERS
from ui.theme import rating_to_hsl


@dataclasses.dataclass(frozen=True)
class BarRow:
    """One labelled horizontal bar: ``width_pct`` (0–100) and ``color`` are trusted CSS, the texts are escaped."""

    label: str
    width_pct: float
    color: str
    value_text: str
    sublabel: str = ""


def bar_rows_html(rows: Sequence[BarRow]) -> str:
    """Render a sequence of :class:`BarRow` as ``.hist-row`` grid rows."""
    parts = []
    for row in rows:
        sub = f'<span class="hist-sub">{html.escape(row.sublabel)}</span>' if row.sublabel else ""
        parts.append(
            f'<div class="hist-row">'
            f'<span class="hist-label">{html.escape(row.label)}{sub}</span>'
            f'<span class="hist-bar"><span class="hist-fill" '
            f'style="width:{row.width_pct:.0f}%;background:{row.color}"></span></span>'
            f'<span class="hist-value">{html.escape(row.value_text)}</span>'
            f"</div>"
        )
    return "".join(parts)


def rating_histogram_html(hist_df: pd.DataFrame) -> str:
    """The half-star rating distribution, grouped under the tier-ladder headers.

    ``hist_df`` is :func:`core.library.rating_histogram`'s frame (all 10 bins
    present, zero-filled). Tiers render best-first (Masterpiece on top) with
    their star range in the header; a zero-count bin keeps its row at width 0
    so the ladder shape stays legible. Returns ``""`` when there is nothing
    counted, so the caller can fall back to an empty state.
    """
    if hist_df.empty or "count" not in hist_df.columns:
        return ""
    total = int(hist_df["count"].sum())
    if total == 0:
        return ""
    counts = {float(r): int(c) for r, c in zip(hist_df["rating"], hist_df["count"], strict=True)}
    widest = max(counts.values()) or 1
    groups = []
    for lo, hi, label in reversed(RATING_TIERS):
        rows = [
            BarRow(
                label=f"★ {star:g}",
                width_pct=counts[star] / widest * 100,
                color=rating_to_hsl(star, scale_max=5.0),
                value_text=f"{counts[star]} · {counts[star] / total:.0%}",
            )
            for star in sorted((s for s in counts if lo <= s <= hi), reverse=True)
        ]
        groups.append(
            f'<div class="hist-group"><div class="hist-group-head">{html.escape(label)}'
            f'<span class="hist-group-range">{lo:g}–{hi:g} ★</span></div>{bar_rows_html(rows)}</div>'
        )
    return f'<div class="hist-list">{"".join(groups)}</div>'


@dataclasses.dataclass(frozen=True)
class SignedBarRow:
    """One signed affinity bar: ``signed_width_pct`` in −100…100 picks the pos/neg fill."""

    marker: str
    label: str
    sublabel: str
    signed_width_pct: float
    value_text: str


def signed_bar_rows_html(rows: Sequence[SignedBarRow]) -> str:
    """Render signed bars through the movie detail page's ``.contrib-*`` vocabulary.

    Same markup as ``pages/movie.py``'s taste breakdown, so the two surfaces
    share one CSS contract: marker + label with a ``contrib-n`` sublabel, a
    pos (green) / neg (red) fill sized by ``|signed_width_pct|``, and the
    numeric value in ``contrib-value``. The marker (✓/✗ sentiment) and the
    fill sign (affinity) are deliberately independent — a liked value can
    carry a negative bar (the tier-ladder [pivot, μ) band).
    """
    parts = []
    for row in rows:
        fill = "pos" if row.signed_width_pct >= 0 else "neg"
        parts.append(
            f'<div class="contrib-row">'
            f'<span class="contrib-label">{html.escape(row.marker)} {html.escape(row.label)}'
            f'<span class="contrib-n">{html.escape(row.sublabel)}</span></span>'
            f'<span class="contrib-bar"><span class="contrib-fill contrib-fill--{fill}" '
            f'style="width:{abs(row.signed_width_pct):.0f}%"></span></span>'
            f'<span class="contrib-value">{html.escape(row.value_text)}</span>'
            f"</div>"
        )
    return "".join(parts)


def affinity_dimension_html(title: str, weight_text: str, rows: Sequence[SignedBarRow]) -> str:
    """One dimension block for the Taste tab: a ``.contrib-dim-head`` header over its signed bars."""
    if not rows:
        return ""
    return (
        f'<div class="contrib-dim"><div class="contrib-dim-head">{html.escape(title)}'
        f'<span class="contrib-weight">{html.escape(weight_text)}</span></div>{signed_bar_rows_html(rows)}</div>'
    )


def decade_profile_html(decades_df: pd.DataFrame) -> str:
    """Films rated per decade: bar width is the count, fill color the mean rating given.

    ``decades_df`` is :func:`core.library.decade_profile`'s frame,
    chronological. The mean also rides in the value text (``"{count} · μ
    {mean:.1f}"``) so the color never carries it alone; a decade with no
    rated films in it shows ``μ —``. Returns ``""`` on an empty frame.
    """
    if decades_df.empty:
        return ""
    widest = int(decades_df["count"].max()) or 1
    rows = [
        BarRow(
            label=f"{int(rec.decade)}s",
            width_pct=int(rec.count) / widest * 100,
            color=rating_to_hsl(rec.mean_rating, scale_max=5.0),
            value_text=f"{int(rec.count)} · μ {'—' if pd.isna(rec.mean_rating) else f'{rec.mean_rating:.1f}'}",
        )
        for rec in decades_df.itertuples()
    ]
    return f'<div class="hist-list">{bar_rows_html(rows)}</div>'
