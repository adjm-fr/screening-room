"""
Shared UI rendering helpers for the cinema dashboard.

Every page imports its visual primitives — movie cards, poster rails, hero
cards, chip filters, KPI strips, empty states, freshness banners, runtime
formatting, rating-to-color conversion, and ICS export — from this module.

The CSS lives in assets/styles.css and is injected on every rerun via
:func:`inject_css`. All HTML rendering uses ``st.markdown(..., unsafe_allow_html=True)``
because Streamlit has no native primitives for the editorial card/rail layouts
this dashboard needs.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pandas as pd
import streamlit as st

log = logging.getLogger(__name__)

_STYLES_PATH = Path(__file__).parent.parent / "assets" / "styles.css"


def inject_css() -> None:
    """Inject ``assets/styles.css`` into the page on every Streamlit rerun.

    Streamlit replaces all rendered output on every rerun, so the ``<style>``
    tag must be re-emitted each time to stay in the DOM.
    """
    try:
        css = _STYLES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("styles.css not found at %s — UI will fall back to Streamlit defaults", _STYLES_PATH)
        return
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ── Formatting helpers ──────────────────────────────────────────────────────


def format_runtime(minutes: int | float | str | None) -> str:
    """Format a runtime in minutes as ``"{h}h{mm}"``.

    Returns ``"—"`` for ``None``, ``NaN``, or zero. If the input is already
    formatted (e.g., "1h 25min"), returns it as-is after stripping whitespace.

    Examples
    --------
    >>> format_runtime(0)
    '—'
    >>> format_runtime(60)
    '1h00'
    >>> format_runtime(132)
    '2h12'
    >>> format_runtime('1h 25min')
    '1h 25min'
    """
    if minutes is None:
        return "—"
    if isinstance(minutes, str):
        minutes = minutes.strip()
        if not minutes:
            return "—"
        if any(c in minutes for c in ("h", "H", "min", "MIN", "hour", "HOUR")):
            return minutes
    try:
        m = int(float(minutes))
    except (ValueError, TypeError):
        return "—"
    if m <= 0:
        return "—"
    hours, rem = divmod(m, 60)
    return f"{hours}h{rem:02d}"


def rating_to_hsl(rating: float | int | None) -> str:
    """Convert a 0-10 rating into an ``hsl()`` color string on an amber heatmap.

    Lightness ramps from 80% (low score) to 40% (high score) at a fixed
    amber hue (36°) and saturation (80%). Returns ``"transparent"`` for
    ``None`` or NaN. Always pair the resulting color with a numeric label
    in the UI to satisfy WCAG 1.4.1 (information not by color alone).
    """
    if rating is None:
        return "transparent"
    try:
        r = float(rating)
    except (ValueError, TypeError):
        return "transparent"
    if pd.isna(r):
        return "transparent"
    r_clamped = max(0.0, min(10.0, r))
    lightness = round(80.0 - r_clamped * 4.0)
    return f"hsl(36 80% {lightness}%)"


# ── Movie card / hero / rail ────────────────────────────────────────────────


def _genre_chips_html(genres_str: str | None) -> str:
    if not isinstance(genres_str, str) or not genres_str:
        return ""
    parts = [p.strip() for p in genres_str.split(",") if p.strip()][:3]
    return "".join(f'<span class="chip chip--genre">{html.escape(p)}</span>' for p in parts)


def _rating_chip_html(rating: float | None) -> str:
    if rating is None or (isinstance(rating, float) and pd.isna(rating)):
        return ""
    color = rating_to_hsl(rating)
    return f'<span class="chip chip--rating" style="background:{color}">★ {float(rating):.1f}</span>'


def _movie_card_html(row: pd.Series, *, size: Literal["sm", "md", "lg"] = "md") -> str:
    """Return the HTML string for a single movie card (poster + meta).

    Pulls ``poster_url``, ``letterboxd_title``/``title``/``french_title``,
    ``directors``, ``runtime_minutes``/``runtime``, ``letterboxd_avg_rating``,
    and ``genres`` from the row when present; missing fields are silently
    skipped. ``size`` controls the CSS modifier class on the card element.
    """
    _title_candidates = [row.get("letterboxd_title"), row.get("french_title"), row.get("title"), row.get("movie")]
    title = next((str(v) for v in _title_candidates if isinstance(v, str) and v), "Untitled")
    directors = next((str(v) for v in [row.get("directors"), row.get("director")] if isinstance(v, str) and v), "")
    runtime = row.get("runtime_minutes")
    if runtime is None or (isinstance(runtime, float) and pd.isna(runtime)):
        runtime = row.get("runtime")
    poster_url = row.get("poster_url")
    rating = row.get("letterboxd_avg_rating")
    genres = row.get("genres")

    poster_html = (
        f'<img class="poster" src="{html.escape(str(poster_url))}" alt="{html.escape(title)} poster" loading="lazy" />'
        if isinstance(poster_url, str) and poster_url
        else '<div class="skeleton skeleton-poster"></div>'
    )
    runtime_chip = f'<span class="chip">{html.escape(format_runtime(runtime))}</span>' if format_runtime(runtime) != "—" else ""
    rating_chip = _rating_chip_html(rating if isinstance(rating, (int, float)) else None)
    genre_chips = _genre_chips_html(genres if isinstance(genres, str) else None)
    sub = html.escape(directors) if directors else ""

    return (
        f'<div class="movie-card movie-card--{size}">'
        f"{poster_html}"
        f'<div class="meta">'
        f'<div class="title">{html.escape(title)}</div>'
        f"{f'<div class="sub">{sub}</div>' if sub else ''}"
        f"<div>{rating_chip}{runtime_chip}</div>"
        f"<div>{genre_chips}</div>"
        f"</div>"
        f"</div>"
    )


def render_movie_card(row: pd.Series, *, size: Literal["sm", "md", "lg"] = "md") -> None:
    """Render a single movie card (poster + meta) as inline HTML."""
    st.markdown(_movie_card_html(row, size=size), unsafe_allow_html=True)


def render_poster_rail(
    rows: pd.DataFrame,
    *,
    title: str,
    empty_icon: str = "🎬",
    empty_title: str = "Nothing here yet",
    empty_hint: str = "Check back when new screenings are scraped.",
) -> None:
    """Render a horizontal scroll rail of movie cards. Falls back to an empty state."""
    if rows.empty:
        render_empty_state(empty_icon, empty_title, empty_hint)
        return

    cards_html = "".join(_movie_card_html(row) for _, row in rows.iterrows())
    st.markdown(
        f'<div class="poster-rail-wrap">'
        f'<div class="poster-rail-title">{html.escape(title)}</div>'
        f'<div class="poster-rail">{cards_html}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render_hero_card(row: pd.Series, *, eyebrow: str | None = None) -> None:
    """Render a large banner-backed hero card for the Home page's "tonight" answer.

    Uses ``banner_url`` (falls back to ``poster_url``) as the background image.
    Title in Playfair Display, eyebrow optional (e.g. "TONIGHT • 19:30"),
    sub-line built from theater + directors. Includes a poster_url alt text
    label for screen readers when no banner is present.
    """

    banner = next((v for v in [row.get("banner_url"), row.get("poster_url")] if isinstance(v, str) and v), "")
    title = next(
        (str(v) for v in [row.get("letterboxd_title"), row.get("french_title"), row.get("title")] if isinstance(v, str) and v),
        "Tonight's pick",
    )
    when = row.get("showtimes")
    when_str = ""
    if when is not None and not (isinstance(when, float) and pd.isna(when)):
        try:
            when_str = pd.to_datetime(when).strftime("%A %d %b · %H:%M")
        except (ValueError, TypeError):
            when_str = ""
    theater = next((str(v) for v in [row.get("theater_name"), row.get("theater_id")] if isinstance(v, str) and v), "")
    directors = next((str(v) for v in [row.get("directors")] if isinstance(v, str) and v), "")
    rating = row.get("letterboxd_avg_rating")

    # Use <img> for the background — CSS background-image is blocked by Streamlit's CSP
    banner_html = f'<img class="hero-bg" src="{html.escape(banner)}" alt="" aria-hidden="true" />' if banner else ""
    eyebrow_str = eyebrow or (when_str or "Up next")
    meta_parts = [p for p in (theater, directors) if p]
    meta_html = " · ".join(html.escape(p) for p in meta_parts)
    rating_chip = _rating_chip_html(rating if isinstance(rating, (int, float)) else None)

    st.markdown(
        f"""
        <div class="hero-card" role="img" aria-label="{html.escape(title)}">
            {banner_html}
            <div class="hero-overlay"></div>
            <div class="hero-body">
                <div class="hero-eyebrow">{html.escape(eyebrow_str)}</div>
                <div class="hero-title h-display">{html.escape(title)}</div>
                <div class="hero-meta">{meta_html}</div>
                <div style="margin-top: 0.75rem;">{rating_chip}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


# ── ICS export ──────────────────────────────────────────────────────────────


def _ics_escape(value: str) -> str:
    """Escape a single ICS TEXT field per RFC 5545 §3.3.11."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n").replace("\r", "")


def to_ics(events: list[dict]) -> bytes:
    """Build an RFC 5545 ICS file from a list of event dicts.

    Each event dict requires ``summary``, ``start`` (datetime-like),
    ``end`` (datetime-like). Optional: ``location``, ``description``, ``uid``.
    Returns UTF-8 bytes with CRLF line endings (per RFC 5545 §3.1).

    Times are written in floating-local form (no Z suffix, no TZID) to
    minimise calendar-import surprises across Google/Apple/Outlook — the
    user's calendar shows them at their local clock time.
    """
    now_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//cinema_dashboard//watchlist//EN",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        start = pd.to_datetime(ev["start"]).strftime("%Y%m%dT%H%M%S")
        end = pd.to_datetime(ev["end"]).strftime("%Y%m%dT%H%M%S")
        uid = ev.get("uid") or f"{uuid4()}@cinema_dashboard"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{start}",
                f"DTEND:{end}",
                f"SUMMARY:{_ics_escape(str(ev['summary']))}",
            ]
        )
        if ev.get("location"):
            lines.append(f"LOCATION:{_ics_escape(str(ev['location']))}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_ics_escape(str(ev['description']))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
