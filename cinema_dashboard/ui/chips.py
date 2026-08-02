"""
Chip-based UI primitives: taste-match badges, filter pills, KPI strips,
empty states, and the data-freshness banner.

``match_chips_html`` is the taste-badge renderer pages pass into
:func:`ui.cards.render_poster_rail`'s ``extra_html_fn`` hook to show the
"◎ {n}% match" badge and "✓ because" chips on ranked rails.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import streamlit as st

from core.taste import TasteProfile, explain
from ui.theme import rating_to_hsl

# ── Match chips (taste badge) ───────────────────────────────────────────────


def match_chips_html(row: pd.Series, profile: TasteProfile) -> str:
    """Render the taste-match row for a card: ``◎ {n}% match`` badge + why-chips.

    The badge background reuses the amber rating heatmap (:func:`ui.theme.rating_to_hsl`,
    0-10 domain) and always pairs color with the numeric label + icon so the
    information never rides on color alone (WCAG 1.4.1). Up to two strictly
    positive contributors from :func:`core.taste.explain` follow as
    ``✓ {label}`` chips. Returns ``""`` when the row has no ``match`` value,
    so callers can interpolate unconditionally.
    """
    match = row.get("match")
    if not isinstance(match, (int, float)) or pd.isna(match):
        return ""
    pct = int(round(float(match)))
    badge = f'<span class="chip chip--match" style="background:{rating_to_hsl(float(match) / 10.0)}">◎ {pct}% match</span>'
    why = "".join(f'<span class="chip chip--why">✓ {html.escape(label)}</span>' for label, _ in explain(row, profile, top_k=2))
    return f'<div class="match-row">{badge}{why}</div>'


# ── KPI strip ───────────────────────────────────────────────────────────────


def render_kpi_strip(kpis: list[tuple[str, str | int | float]]) -> None:
    """Render a row of KPI cards in equal-width columns.

    Uses native ``st.columns`` so widths reflow on narrow viewports.
    """
    if not kpis:
        return
    cols = st.columns(len(kpis))
    for col, (label, value) in zip(cols, kpis, strict=True):
        with col:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">{html.escape(label)}</div>
                    <div class="kpi-value">{html.escape(str(value))}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# ── Chip filter (st.pills wrapper) ──────────────────────────────────────────


def render_chip_filter(
    label: str,
    options: list[str],
    *,
    key: str,
    selection_mode: Literal["single", "multi"] = "multi",
    default: list[str] | str | None = None,
    on_change: Callable[[], None] | None = None,
) -> list[str]:
    """Wrap ``st.pills`` with normalised return type and consistent labelling.

    Always returns a ``list[str]`` (empty when nothing is selected) regardless
    of selection mode, so callers don't have to special-case the single-mode
    ``str | None`` return.
    """
    if not options:
        return []
    selection = st.pills(
        label,
        options=options,
        selection_mode=selection_mode,
        default=default,
        key=key,
        on_change=on_change,
    )
    if selection is None:
        return []
    if isinstance(selection, str):
        return [selection]
    return list(selection)


# ── Empty state ─────────────────────────────────────────────────────────────


def render_empty_state(
    icon: str,
    title: str,
    hint: str,
    cta: tuple[str, str] | None = None,
) -> None:
    """Render a designed empty state for "no data" surfaces.

    ``cta`` is ``(label, page_path)`` for an optional ``st.page_link`` below the hint.
    """
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="empty-icon" aria-hidden="true">{html.escape(icon)}</div>
            <div class="empty-title">{html.escape(title)}</div>
            <div class="empty-hint">{html.escape(hint)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if cta is not None:
        cta_label, cta_path = cta
        st.page_link(cta_path, label=cta_label)


# ── Freshness banner ────────────────────────────────────────────────────────


def render_freshness_banner(*paths: Path | str | None) -> None:
    """Show a green/yellow/red freshness pill from the most-recent parquet mtime.

    - green (fresh): newest file <24h old
    - yellow (aging): newest file 1-7d old
    - red (stale): newest file >7d old or no files exist
    """
    valid_paths = [Path(p) for p in paths if p and Path(p).exists()]
    if not valid_paths:
        st.markdown(
            '<span class="freshness-banner freshness--stale"><span class="freshness-dot"></span>No data yet</span>',
            unsafe_allow_html=True,
        )
        return
    newest = max(p.stat().st_mtime for p in valid_paths)
    age = datetime.now().timestamp() - newest
    age_h = age / 3600
    if age_h < 24:
        cls, label = "freshness--fresh", f"Updated {int(age_h)}h ago"
    elif age_h < 24 * 7:
        cls, label = "freshness--aging", f"Updated {int(age_h / 24)}d ago"
    else:
        cls, label = "freshness--stale", f"Stale · {int(age_h / 24)}d old"
    st.markdown(
        f'<span class="freshness-banner {cls}"><span class="freshness-dot"></span>{html.escape(label)}</span>',
        unsafe_allow_html=True,
    )
