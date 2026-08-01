"""
Movie detail page — everything the pipeline knows about one film.

Reached from every card in the app: :func:`ui.movie_href` renders each
poster, rail entry and hero as a link to ``?movie=<slug>``, and ``app.py``
routes that query parameter here instead of running the selected navigation
page. Unlike the five ``st.Page`` files this module therefore does **not** call
``main()`` at import time — it is called by ``app.py`` with the requested slug,
which is what lets a second navigation to a different film re-render (a
module-level call would only ever run once per process, on first import).

The page reads ``data_letterboxd.parquet`` (a superset of the ratings and
watchlist parquets), so every film the app can show a card for has a detail
page — including the few hundred Allocine-enriched films that are on neither
list. Sections are **omitted, never rendered empty**: the cache's coverage is
uneven (``trailer_url`` is null for ~2/3 of films, ``cast`` for ~2/5, ``tagline``
for ~3/10 — the first two are an in-flight TMDB backfill), and a page of empty
headings reads as broken data rather than as absent data.
"""

from __future__ import annotations

import html
import logging

import pandas as pd
import streamlit as st

from config import settings
from core.movie import THEME_COLUMNS, load_movie, movie_screenings, similar_films, split_values
from core.taste import (
    WEIGHTS,
    TasteProfile,
    build_affinity,
    contributions,
    match_from_raw,
    quality_prior,
)
from sources.loader import (
    attach_streaming,
    build_watchlist_showtimes,
    future_showtimes,
    get_paths,
    load_letterboxd_cache,
    load_ratings,
    load_showtimes,
    load_watchlist,
)
from ui import (
    format_runtime,
    rating_to_hsl,
    render_empty_state,
    render_poster_rail,
    screening_end,
    to_ics,
)
from ui.cards import _streaming_badges_html, _user_rating_chip_html

log = logging.getLogger(__name__)

#: Human labels for the taste dimensions, in ``WEIGHTS`` order.
_DIMENSION_LABELS = {
    "directors": "Director",
    "genres": "Genre",
    "themes": "Theme",
    "cast": "Cast",
    "decade": "Decade",
    "country": "Country",
    "language": "Language",
}

#: Credit fields rendered as a plain "label — value" list, in billing order.
_CREDIT_FIELDS = (
    ("directors", "Director"),
    ("writers", "Writer"),
    ("producers", "Producer"),
    ("studio", "Studio"),
    ("cast", "Cast"),
)


def _text(movie: pd.Series, column: str) -> str:
    """Return a stripped string cell, or ``""`` for null/blank/non-string."""
    value = movie.get(column)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _title_of(movie: pd.Series) -> str:
    """The film's display title: the Letterboxd one, else the French retitle."""
    return _text(movie, "title") or _text(movie, "french_title") or "Untitled"


def _render_back_link() -> None:
    """A same-tab link back to the page the visitor came from.

    ``href="?"`` keeps the current path and empties the query string, so it
    returns to whichever navigation page was showing rather than always to
    Home. Browser back works too — the detail view is a real URL.

    Rendered as a ghost pill (``.detail-back``) rather than a bare anchor. The
    arrow is its own span so it can nudge left on hover independently of the
    label; it is ``aria-hidden`` because the label already carries the meaning.
    """
    st.markdown(
        '<a class="detail-back" href="?" target="_self">'
        '<span class="detail-back__arrow" aria-hidden="true">←</span>Back to the dashboard'
        "</a>",
        unsafe_allow_html=True,
    )


def _render_hero(movie: pd.Series) -> None:
    """Banner-backed masthead: title, alternate titles, year · runtime · country, tagline."""
    banner = _text(movie, "banner_url") or _text(movie, "poster_url")
    title = _title_of(movie)

    facts: list[str] = []
    year = movie.get("release_year")
    if year is not None and not pd.isna(year):
        facts.append(str(int(year)))
    runtime = format_runtime(movie.get("runtime"))
    if runtime != "—":
        facts.append(runtime)
    country = _text(movie, "country")
    if country:
        facts.append(country)

    alternates = [t for t in (_text(movie, "original_title"), _text(movie, "french_title")) if t and t != title]
    tagline = _text(movie, "tagline")

    parts = [
        f'<div class="hero-eyebrow">{html.escape(" · ".join(facts))}</div>' if facts else "",
        f'<div class="hero-title h-display">{html.escape(title)}</div>',
        f'<div class="hero-meta">{html.escape(" · ".join(dict.fromkeys(alternates)))}</div>' if alternates else "",
        f'<div class="detail-tagline">{html.escape(tagline)}</div>' if tagline else "",
    ]
    banner_html = (
        f'<img class="hero-bg" src="{html.escape(banner)}" alt="" aria-hidden="true" loading="lazy" />' if banner else ""
    )
    st.markdown(
        f'<div class="hero-card detail-hero">{banner_html}<div class="hero-overlay"></div>'
        f'<div class="hero-body">{"".join(p for p in parts if p)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_verdict(movie: pd.Series, on_watchlist: bool) -> None:
    """Your standing with this film: your star rating, else watchlisted, else untracked.

    The rating is the user's own 0–5 star value, so it renders through the green
    ``chip--user-rating`` (Letterboxd's convention for "your rating") rather than
    the amber community chip beside it.
    """
    st.markdown('<div class="detail-section-title">Your verdict</div>', unsafe_allow_html=True)
    rating = movie.get("user_rating")
    if isinstance(rating, (int, float)) and not pd.isna(rating):
        st.markdown(
            f'{_user_rating_chip_html(float(rating))}<span class="detail-verdict-note">you rated this</span>',
            unsafe_allow_html=True,
        )
    elif on_watchlist:
        st.markdown('<span class="chip chip--why">🔖 On your watchlist</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="chip">Not tracked yet</span>', unsafe_allow_html=True)


def _contribution_rows_html(movie: pd.Series, profile: TasteProfile) -> str:
    """Render every per-value taste contribution as a signed, labelled bar."""
    terms = contributions(movie, profile)
    if not terms:
        return ""
    widest = max(abs(term.contribution) for term in terms) or 1.0
    by_dimension: dict[str, list[str]] = {}
    for term in terms:
        marker, sentiment = ("✓", "liked") if term.liked else ("✗", "disliked")
        fill = "pos" if term.contribution >= 0 else "neg"
        by_dimension.setdefault(term.dimension, []).append(
            f'<div class="contrib-row">'
            f'<span class="contrib-label">{marker} {html.escape(term.value)}'
            f'<span class="contrib-n">{sentiment} · {term.n_rated} rated</span></span>'
            f'<span class="contrib-bar"><span class="contrib-fill contrib-fill--{fill}" '
            f'style="width:{abs(term.contribution) / widest * 100:.0f}%"></span></span>'
            f'<span class="contrib-value">{term.contribution:+.3f}</span>'
            f"</div>"
        )
    blocks = "".join(
        f'<div class="contrib-dim"><div class="contrib-dim-head">{html.escape(_DIMENSION_LABELS.get(dim, dim))}'
        f'<span class="contrib-weight">weight {WEIGHTS[dim]:g}</span></div>{"".join(rows)}</div>'
        for dim, rows in by_dimension.items()
    )
    return f'<div class="contrib-list">{blocks}</div>'


def _render_match(movie: pd.Series, profile: TasteProfile | None) -> None:
    """The ``◎ n% match`` badge plus the full arithmetic behind it.

    Cards show the top two liked contributors; here every term that fed the
    score is listed, disliked ones included (flagged, not hidden), followed by
    the community-quality prior and the logistic that maps the raw total onto
    the badge — so the number on the card is reproducible from this page.
    """
    if profile is None or profile.is_empty:
        return
    terms = contributions(movie, profile)
    prior = quality_prior(movie)
    if not terms and prior is None:
        return

    raw = sum(term.contribution for term in terms) + (prior or 0.0)
    match = match_from_raw(raw)
    st.markdown('<div class="detail-section-title">Taste match</div>', unsafe_allow_html=True)
    st.markdown(
        f'<span class="chip chip--match" style="background:{rating_to_hsl(match / 10.0)}">◎ {round(match)}% match</span>',
        unsafe_allow_html=True,
    )
    with st.expander("How this number is built"):
        st.markdown(_contribution_rows_html(movie, profile), unsafe_allow_html=True)
        prior_text = f"{prior:+.3f}" if prior is not None else "not counted (no community rating)"
        st.markdown(
            f'<div class="contrib-total">Community-quality prior: <b>{prior_text}</b><br>'
            f"Raw total <b>{raw:+.3f}</b> → logistic → <b>{round(match)}% match</b></div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Each value's share is its weight × affinity ÷ the film's known values in that dimension. "
            "Affinities are centred on your own average rating; liked/disliked is judged against the "
            "watchable/good boundary of your rating ladder, which is why a value can be liked and still "
            "score negative."
        )


def _render_credits(movie: pd.Series) -> None:
    """Directors, writers, producers, studio and billed cast — each row omitted when null."""
    rows = [(label, _text(movie, column)) for column, label in _CREDIT_FIELDS]
    present = [(label, value) for label, value in rows if value]
    if not present:
        return
    st.markdown('<div class="detail-section-title">Credits</div>', unsafe_allow_html=True)
    st.markdown(
        '<dl class="detail-credits">'
        + "".join(f"<dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd>" for label, value in present)
        + "</dl>",
        unsafe_allow_html=True,
    )


def _render_themes(movie: pd.Series) -> None:
    """Themes and mini-themes as chips (deduped, source-order preserved)."""
    themes = list(dict.fromkeys(value for column in THEME_COLUMNS for value in split_values(movie.get(column))))
    if not themes:
        return
    st.markdown('<div class="detail-section-title">Themes</div>', unsafe_allow_html=True)
    st.markdown(
        "".join(f'<span class="chip chip--theme">{html.escape(theme)}</span>' for theme in themes),
        unsafe_allow_html=True,
    )


def _render_screenings(shows: pd.DataFrame, movie: pd.Series) -> None:
    """Upcoming screenings grouped by theater, each with a one-click ``.ics``.

    Calendar blocks are sized by :func:`ui.screening_end` — the same
    helper the calendar page's ICS and CSV exports use — so a single-screening
    download and a bulk export never disagree about when a film ends.
    """
    if shows.empty:
        return
    st.markdown('<div class="detail-section-title">Upcoming screenings</div>', unsafe_allow_html=True)
    title = _title_of(movie)
    for theater, group in shows.groupby("theater_name", sort=True):
        st.markdown(f'<div class="theater-name">{html.escape(str(theater))}</div>', unsafe_allow_html=True)
        for index, row in group.iterrows():
            showtime = pd.to_datetime(row["showtimes"], errors="coerce")
            if pd.isna(showtime):
                continue
            slot, button = st.columns([3, 1], vertical_alignment="center")
            slot.markdown(
                f'<div class="showtime-badge">{html.escape(showtime.strftime("%A %d %B · %H:%M"))}</div>',
                unsafe_allow_html=True,
            )
            event = {
                "summary": title,
                "start": showtime,
                "end": screening_end(row, showtime),
                "location": str(row.get("theater_name") or row.get("theater_id") or ""),
                "description": f"Directors: {row.get('directors') or 'N/A'}",
                "uid": f"{index}-{int(showtime.timestamp())}@cinema_dashboard",
            }
            button.download_button(
                "📅 .ics",
                data=to_ics([event]),
                file_name=f"{movie.get('slug', 'screening')}-{showtime:%Y%m%d-%H%M}.ics",
                mime="text/calendar",
                key=f"ics_{index}_{showtime:%Y%m%d%H%M}",
                help="Add this screening to your calendar",
            )


def _render_streaming(movie: pd.Series, movies_output: str, subscribed: set[str] | frozenset[str] | None) -> None:
    """Provider chips for this film, via the shared streaming cache join."""
    if "tmdb_id" not in movie.index:
        return
    enriched = attach_streaming(pd.DataFrame([movie]), movies_output).iloc[0]
    badges = _streaming_badges_html(enriched.get("flatrate"), enriched.get("free"), subscribed)
    if not badges:
        return
    st.markdown('<div class="detail-section-title">Streaming in France</div>', unsafe_allow_html=True)
    st.markdown(badges, unsafe_allow_html=True)


def _render_trailer(movie: pd.Series) -> None:
    """Embed the trailer when one is cached (it is null for ~2/3 of films)."""
    trailer_url = _text(movie, "trailer_url")
    if not trailer_url:
        return
    st.markdown('<div class="detail-section-title">Trailer</div>', unsafe_allow_html=True)
    st.video(trailer_url)


def _render_links(movie: pd.Series) -> None:
    """Out-links to Letterboxd / IMDB / TMDB, each omitted when the URL is null."""
    links = [
        (label, _text(movie, column))
        for column, label in (("letterboxd_url", "Letterboxd"), ("imdb_url", "IMDB"), ("tmdb_url", "TMDB"))
    ]
    present = [(label, url) for label, url in links if url]
    if not present:
        return
    st.markdown(
        '<div class="detail-links">'
        + "".join(
            f'<a class="chip chip--trailer" href="{html.escape(url)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(label)} ↗</a>'
            for label, url in present
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_not_found(slug: str | None) -> None:
    """Designed empty state for a missing, blank or unknown slug."""
    _render_back_link()
    render_empty_state(
        "🔍",
        "No film at this link",
        f"“{slug}” isn’t in your Letterboxd cache." if slug else "This link is missing a film reference.",
    )


def main(slug: str | None) -> None:
    """Render the detail page for ``slug``, or a designed empty state."""
    movies_path, showtimes_path, _ = get_paths()
    if not movies_path or not (movies_path / "data_letterboxd.parquet").exists():
        _render_back_link()
        render_empty_state(
            "📥",
            "Letterboxd data missing",
            "Run `python main.py` in `movies_management` to build data_letterboxd.parquet.",
        )
        return

    cache_df = load_letterboxd_cache(str(movies_path))
    ratings_df = load_ratings(str(movies_path)) if (movies_path / "ratings_with_letterboxd.parquet").exists() else pd.DataFrame()
    movie = load_movie(cache_df, ratings_df, slug or "")
    if movie is None:
        _render_not_found(slug)
        return

    _render_back_link()
    _render_hero(movie)

    watchlist_df = (
        load_watchlist(str(movies_path)) if (movies_path / "watchlist_with_letterboxd.parquet").exists() else pd.DataFrame()
    )
    on_watchlist = "slug" in watchlist_df.columns and bool((watchlist_df["slug"] == movie["slug"]).any())
    profile = build_affinity(ratings_df) if not ratings_df.empty else None

    poster_col, detail_col = st.columns([1, 2], gap="large")
    with poster_col:
        poster = _text(movie, "poster_url")
        if poster:
            st.image(poster, caption=f"{_title_of(movie)} poster", width="stretch")
        _render_verdict(movie, on_watchlist)
        _render_streaming(movie, str(movies_path), settings.streaming_service_slugs)
        _render_links(movie)

    with detail_col:
        description = _text(movie, "description")
        if description:
            st.markdown('<div class="detail-section-title">Synopsis</div>', unsafe_allow_html=True)
            st.markdown(f'<p class="detail-synopsis">{html.escape(description)}</p>', unsafe_allow_html=True)
        _render_match(movie, profile)
        _render_credits(movie)
        _render_themes(movie)

    # Screenings come from the same films↔showtimes join the rest of the app
    # uses, but keyed on the *cache* rather than the watchlist so a rated or
    # merely-cached film still lists its screenings.
    if showtimes_path and showtimes_path.exists():
        try:
            shows = build_watchlist_showtimes(future_showtimes(load_showtimes(str(showtimes_path))), cache_df)
        except Exception as exc:  # a malformed/absent showtimes file must not take the page down
            log.warning("Showtimes unavailable on the movie detail page: %s", exc)
        else:
            _render_screenings(movie_screenings(shows, str(movie["slug"])), movie)

    _render_trailer(movie)

    similar = similar_films(cache_df, movie)
    if not similar.empty:
        render_poster_rail(similar.rename(columns={"title": "letterboxd_title"}), title="More like this")
