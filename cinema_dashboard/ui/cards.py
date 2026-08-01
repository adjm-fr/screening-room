"""
Movie card, hero card, and poster-rail rendering for the cinema dashboard.

Every page imports its movie-display primitives — cards, poster rails, hero
cards — from this module. All HTML rendering uses
``st.markdown(..., unsafe_allow_html=True)`` because Streamlit has no native
primitives for the editorial card/rail layouts this dashboard needs.

Cards and hero cards are **links**: whenever the row carries a Letterboxd slug
they render an anchor to ``?movie=<slug>`` (see :func:`ui.theme.movie_href`),
which ``app.py`` routes to the movie detail page. Wrapping it here rather than
at the call sites is what makes every surface — home rails, calendar day
rails, streaming rails, chat's pinned recommendations — clickable for free.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Literal

import pandas as pd
import streamlit as st
from sources.loader import coerce_str_list
from sources.streaming import display_name, load_display_names_catalog

from ui.chips import render_empty_state
from ui.theme import format_runtime, movie_href, rating_to_hsl, row_slug

# ── Movie card / hero / rail ────────────────────────────────────────────────


def _genre_chips_html(genres_str: str | None) -> str:
    if not isinstance(genres_str, str) or not genres_str:
        return ""
    parts = [p.strip() for p in genres_str.split(",") if p.strip()][:3]
    return "".join(f'<span class="chip chip--genre">{html.escape(p)}</span>' for p in parts)


def _streaming_badges_html(
    flatrate: object,
    free: object,
    subscribed: set[str] | frozenset[str] | None,
) -> str:
    """Render streaming-availability chips for a single film.

    Subscribed services where the film is on flatrate render as filled chips
    (``.chip--streaming``). Free-to-watch providers (Arte.tv, France.tv, …)
    render as ``.chip--streaming-free`` chips regardless of subscription —
    they're watchable by everyone — with the word "free" baked into the chip
    text itself (not just the color) so the distinction holds for colorblind
    users and screen readers (WCAG 1.4.1). Returns ``""`` when no chips would
    render, so callers can safely interpolate the result unconditionally.

    ``flatrate``/``free`` accept lists, numpy arrays, ``None``, or ``NaN``
    (parquet object columns surface as any of these depending on the engine);
    everything else is treated as empty.
    """
    sub: frozenset[str] = frozenset(subscribed or ())
    flat = coerce_str_list(flatrate)
    subscribed_flat = [p for p in flat if p in sub]
    free_list = coerce_str_list(free)
    if not subscribed_flat and not free_list:
        return ""
    catalogue = load_display_names_catalog()
    chips: list[str] = [
        f'<span class="chip chip--streaming">{html.escape(display_name(slug, catalogue))}</span>' for slug in subscribed_flat
    ]
    chips.extend(
        f'<span class="chip chip--streaming-free">{html.escape(display_name(slug, catalogue))} (free)</span>'
        for slug in free_list
    )
    return f'<div class="streaming-row">{"".join(chips)}</div>'


def _rating_chip_html(rating: float | None) -> str:
    if rating is None or (isinstance(rating, float) and pd.isna(rating)):
        return ""
    # Letterboxd averages are on the same 0-5 star scale as the user's own rating.
    color = rating_to_hsl(rating, scale_max=5.0)
    return f'<span class="chip chip--rating" title="Letterboxd average" style="background:{color}">★ {float(rating):.1f}</span>'


def _user_rating_chip_html(rating: float | None) -> str:
    """Green chip for the user's own star rating (Letterboxd shows your rating in green).

    Uses a green heatmap on the 0-5 star scale; paired with an ``aria-label`` so
    it reads distinctly from the amber Letterboxd-average chip for screen readers
    and colorblind users (WCAG 1.4.1)."""
    if rating is None or (isinstance(rating, float) and pd.isna(rating)):
        return ""
    color = rating_to_hsl(rating, hue=145, scale_max=5.0)
    return (
        f'<span class="chip chip--rating chip--user-rating" title="Your rating" '
        f'aria-label="Your rating: {float(rating):.1f} out of 5" '
        f'style="background:{color}">★ {float(rating):.1f}</span>'
    )


def _movie_card_html(
    row: pd.Series,
    *,
    size: Literal["sm", "md", "lg"] = "md",
    extra_html: str = "",
    subscribed: set[str] | frozenset[str] | None = None,
) -> str:
    """Return the HTML string for a single movie card (poster + meta).

    Pulls ``poster_url``, ``letterboxd_title``/``title``/``french_title``,
    ``directors``, ``runtime_minutes``/``runtime``, ``letterboxd_avg_rating``,
    ``genres``, and ``trailer_url`` from the row when present; missing fields
    are silently skipped. ``size`` controls the CSS modifier class on the
    card element.

    When the row carries a slug (:func:`row_slug`) the **title becomes a link**
    to that film's detail page and the card gains ``.movie-card--linked``,
    whose ``::after`` overlay stretches the title link's hit area across the
    whole card. That indirection is deliberate: a card already contains a real
    ``<a>`` (the trailer chip), and nesting anchors is invalid HTML that
    browsers silently unnest. One anchor per card also keeps the tab order to
    one stop and gives screen readers the film title as the link name.
    """
    _title_candidates = [row.get("letterboxd_title"), row.get("french_title"), row.get("title"), row.get("movie")]
    title = next((str(v) for v in _title_candidates if isinstance(v, str) and v), "Untitled")
    directors = next((str(v) for v in [row.get("directors"), row.get("director")] if isinstance(v, str) and v), "")
    runtime = row.get("runtime_minutes")
    if runtime is None or (isinstance(runtime, float) and pd.isna(runtime)):
        runtime = row.get("runtime")
    poster_url = row.get("poster_url")
    rating = row.get("letterboxd_avg_rating")
    user_rating = row.get("user_rating")
    genres = row.get("genres")
    trailer_url = row.get("trailer_url")

    poster_html = (
        f'<img class="poster" src="{html.escape(str(poster_url))}" alt="{html.escape(title)} poster" loading="lazy" />'
        if isinstance(poster_url, str) and poster_url
        else '<div class="skeleton skeleton-poster"></div>'
    )
    runtime_chip = f'<span class="chip">{html.escape(format_runtime(runtime))}</span>' if format_runtime(runtime) != "—" else ""
    rating_chip = _rating_chip_html(rating if isinstance(rating, (int, float)) else None)
    user_rating_chip = _user_rating_chip_html(user_rating if isinstance(user_rating, (int, float)) else None)
    genre_chips = _genre_chips_html(genres if isinstance(genres, str) else None)
    trailer_chip = (
        f'<a class="chip chip--trailer" href="{html.escape(trailer_url)}" target="_blank" rel="noopener noreferrer">▶ Trailer</a>'
        if isinstance(trailer_url, str) and trailer_url
        else ""
    )
    streaming_chips = _streaming_badges_html(row.get("flatrate"), row.get("free"), subscribed)
    sub = html.escape(directors) if directors else ""

    slug = row_slug(row)
    linked_class = " movie-card--linked" if slug else ""
    title_html = (
        f'<a class="movie-card-link" href="{movie_href(slug)}" target="_self">{html.escape(title)}</a>'
        if slug
        else html.escape(title)
    )

    return (
        f'<div class="movie-card movie-card--{size}{linked_class}">'
        f"{poster_html}"
        f'<div class="meta">'
        f'<div class="title">{title_html}</div>'
        f"{f'<div class="sub">{sub}</div>' if sub else ''}"
        f"<div>{user_rating_chip}{rating_chip}{runtime_chip}</div>"
        f"<div>{genre_chips}{trailer_chip}</div>"
        f"{streaming_chips}"
        f"{extra_html}"
        f"</div>"
        f"</div>"
    )


def render_movie_card(
    row: pd.Series,
    *,
    size: Literal["sm", "md", "lg"] = "md",
    subscribed: set[str] | frozenset[str] | None = None,
) -> None:
    """Render a single movie card (poster + meta) as inline HTML.

    ``subscribed`` enables the streaming-availability badge row when the row
    carries a ``flatrate`` column (populated by
    :func:`sources.loader.attach_streaming`).
    """
    st.markdown(_movie_card_html(row, size=size, subscribed=subscribed), unsafe_allow_html=True)


def render_poster_rail(
    rows: pd.DataFrame,
    *,
    title: str,
    empty_icon: str = "🎬",
    empty_title: str = "Nothing here yet",
    empty_hint: str = "Check back when new screenings are scraped.",
    subscribed: set[str] | frozenset[str] | None = None,
    extra_html_fn: Callable[[pd.Series], str] | None = None,
) -> None:
    """Render a horizontal scroll rail of movie cards. Falls back to an empty state.

    ``extra_html_fn`` is called per row and its result injected at the bottom
    of each card's meta block (e.g. :func:`ui.chips.match_chips_html` for taste badges).
    """
    if rows.empty:
        render_empty_state(empty_icon, empty_title, empty_hint)
        return

    cards_html = "".join(
        _movie_card_html(row, subscribed=subscribed, extra_html=extra_html_fn(row) if extra_html_fn else "")
        for _, row in rows.iterrows()
    )
    st.markdown(
        f'<div class="poster-rail-wrap">'
        f'<div class="poster-rail-title">{html.escape(title)}</div>'
        f'<div class="poster-rail">{cards_html}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def render_hero_card(
    row: pd.Series,
    *,
    eyebrow: str | None = None,
    subscribed: set[str] | frozenset[str] | None = None,
) -> None:
    """Render a large banner-backed hero card for the Home page's "tonight" answer.

    Uses ``banner_url`` (falls back to ``poster_url``) as the background image.
    Title in Playfair Display, eyebrow optional (e.g. "TONIGHT • 19:30"),
    sub-line built from theater + directors.

    When the row carries a slug (:func:`row_slug`) an absolutely-positioned
    overlay anchor covering the whole hero links to that film's detail page.
    It is a sibling of ``.hero-body`` rather than a wrapper around the title
    because ``.hero-body`` is itself positioned — an ``::after`` overlay opened
    inside it would only stretch across the text block, not the banner. The
    empty anchor carries an ``aria-label`` so it still has an accessible name.

    The container deliberately carries **no** ``role="img"``: that role hides
    an element's contents from assistive technology, which would have muted the
    title, the meta line, and the link below it. The banner ``<img>`` is marked
    decorative instead (``alt=""`` + ``aria-hidden``), and the title is read as
    ordinary text.
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
    eyebrow_base = eyebrow or when_str or "Up next"
    eyebrow_str = f"{eyebrow_base} · {theater}" if theater else eyebrow_base
    meta_html = html.escape(directors) if directors else ""
    rating_chip = _rating_chip_html(rating if isinstance(rating, (int, float)) else None)
    streaming_chips = _streaming_badges_html(row.get("flatrate"), row.get("free"), subscribed)
    rating_html = f'<div style="margin-top: 0.75rem;">{rating_chip}</div>' if rating_chip else ""
    meta_block = f'<div class="hero-meta">{meta_html}</div>' if meta_html else ""

    body_parts = "".join(
        p
        for p in (
            f'<div class="hero-eyebrow">{html.escape(eyebrow_str)}</div>',
            f'<div class="hero-title h-display">{html.escape(title)}</div>',
            meta_block,
            rating_html,
            streaming_chips,
        )
        if p
    )
    slug = row_slug(row)
    link_html = (
        f'<a class="hero-link" href="{movie_href(slug)}" target="_self" '
        f'aria-label="{html.escape(title)} — open film details"></a>'
        if slug
        else ""
    )
    st.markdown(
        f'<div class="hero-card{" hero-card--linked" if slug else ""}">'
        f"{banner_html}"
        f'<div class="hero-overlay"></div>'
        f'<div class="hero-body">{body_parts}</div>'
        f"{link_html}"
        f"</div>",
        unsafe_allow_html=True,
    )
