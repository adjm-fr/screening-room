"""
Screening in Paris — everything screening this week, not just the watchlist.

Every other showtimes-driven page (Home, Watchlist Showtimes) is built on
``sources.loader.build_watchlist_showtimes``, an inner join that only ever
surfaces films already on the watchlist. Measured against the real parquets:
250 films screen across 13 Paris theaters in a week and that join surfaces 14
of them. This page joins the full showtimes against the Letterboxd metadata
cache instead (``sources.discover.build_screenings``), labels every film
"new to you" / "watchlist" / "seen", and taste-ranks them with the same ranker
(``core.taste``) every other rail uses — so the badge and "because" chips mean
the same thing here as on Home.

The page is **curated sections, not a filter wall**: one rail per question
worth asking of a week's programme ("what's new to me?", "what did I dislike
that my taste now says I'd like?", "what would I happily see again?"). There
is no browse-everything fallback and no theater/day picker — the one control
on the page is "Only times I'm free" (:func:`ui.render_free_time_filter`, the
same widget the Watchlist Showtimes page mounts), which narrows every section
to screenings the user can actually attend before any of them render. Every
card lists its upcoming showtimes (day, time, theater), capped and suffixed
"+N more" for a wide release — mirrors the Watchlist Showtimes day rails'
``.showtime-badge`` treatment, just per-card instead of per-day since these
rails aren't grouped by date.
"""

from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from core.taste import attach_match, build_affinity
from sources.discover import build_screenings
from sources.loader import future_showtimes, get_paths, load_letterboxd_cache, load_ratings, load_showtimes, load_watchlist
from ui import match_chips_html, render_empty_state, render_free_time_filter, render_kpi_strip, render_poster_rail

#: How many cards the "new to you" / "second chance" rails show.
TOP_MATCHES_SIZE = 12

#: "Worth a rewatch!" gets a bigger cap than the other two rails — it draws on
#: the user's own already-loved films (61 qualified in a sample week), not a
#: ranker guess, so there's less risk of the rail padding itself with weak picks.
REWATCH_RAIL_SIZE = 24

#: "Worth a rewatch!" — films you rated at least this. On the ratings ladder
#: (see CLAUDE.md) 3.5–4 is "must watch" and 4.5–5 "masterpiece", so 4.0 is
#: the floor of "I'd happily sit through it again".
REWATCH_MIN_RATING = 4.0

#: "Worth a second chance?" — films you rated *below* this. 2.5 is the ladder's
#: bottom of "good", so under it is the genuinely-didn't-land band.
RETRY_MAX_RATING = 2.5

#: …but only where the ranker disagrees with that verdict this strongly. The
#: match is a 0–100 logistic (``core.taste.match_from_raw``) and 70 sits near
#: the top of the observed distribution, so the rail stays short and pointed
#: instead of re-listing everything you were lukewarm on.
RETRY_MIN_MATCH = 70.0

#: Showtime badges per card. A wide release can carry dozens of screenings in
#: a week (measured max: 86) — capped with a "+N more" suffix rather than
#: either truncating silently or letting one card's badge list dominate the rail.
MAX_SHOWTIME_BADGES = 6


def _dedupe_key(row: pd.Series) -> str:
    """A film's identity for de-duplicating multiple showtimes into one card.

    Prefers the Letterboxd slug (stable, unique) and falls back to the
    Allocine display title, for the degenerate case of a metadata cache
    carrying no ``slug`` column at all.
    """
    slug = row.get("letterboxd_slug")
    if isinstance(slug, str) and slug:
        return slug
    return str(row.get("french_title") or "")


def _showtime_badges_html(dedupe_key: str, showtimes_by_key: dict[str, pd.DataFrame]) -> str:
    """One ``.showtime-badge`` div per upcoming screening for this film, earliest first.

    ``showtimes_by_key`` groups the (already free-time-filtered) screenings
    frame by :func:`_dedupe_key` — every showtime row for the film, not just
    the single representative row a rail dedupes down to. Capped at
    :data:`MAX_SHOWTIME_BADGES` with a "+N more" line rather than listing a
    wide release's full run.
    """
    group = showtimes_by_key.get(dedupe_key)
    if group is None or group.empty:
        return ""
    ordered = group.assign(_dt=pd.to_datetime(group["showtimes"])).sort_values("_dt")
    lines = ""
    for _, row in ordered.head(MAX_SHOWTIME_BADGES).iterrows():
        label = row["_dt"].strftime("%a %d %b, %H:%M")
        theater = str(row.get("theater_name") or "")
        line = _html.escape(label)
        if theater:
            line += f" · {_html.escape(theater)}"
        lines += f'<div class="showtime-badge">{line}</div>'
    remaining = len(ordered) - MAX_SHOWTIME_BADGES
    if remaining > 0:
        lines += f'<div class="showtime-badge">+{remaining} more</div>'
    return lines


def main() -> None:
    st.markdown('<h1 class="h-display" style="font-size:2.4rem;">Screening in Paris</h1>', unsafe_allow_html=True)
    st.caption("Everything screening across your tracked theaters this week — not only your watchlist.")

    movies_path, showtimes_path, _ = get_paths()
    if not movies_path or not showtimes_path:
        render_empty_state(
            "⚙️",
            "Configure your data paths",
            "Set OUTPUT_PATH and ALLOCINE_OUTPUT_PATH in .env to populate the dashboard.",
        )
        return
    if not (movies_path / "data_letterboxd.parquet").exists() or not showtimes_path.exists():
        render_empty_state(
            "🎬",
            "No data yet",
            "Run the orchestrate.py CLI (or Dagster) to scrape the metadata cache + showtimes.",
        )
        return

    try:
        showtimes_df = load_showtimes(str(showtimes_path))
        cache_df = load_letterboxd_cache(str(movies_path))
        ratings_df = (
            load_ratings(str(movies_path)) if (movies_path / "ratings_with_letterboxd.parquet").exists() else pd.DataFrame()
        )
        watchlist_df = (
            load_watchlist(str(movies_path)) if (movies_path / "watchlist_with_letterboxd.parquet").exists() else pd.DataFrame()
        )
    except Exception as exc:
        st.error(f"Failed to load data: {exc}")
        return

    showtimes_df = future_showtimes(showtimes_df)
    if showtimes_df.empty:
        render_empty_state(
            "🍿",
            "No screenings this week",
            "Showtimes refresh Tuesday morning — check back soon.",
        )
        return

    screenings = build_screenings(showtimes_df, cache_df, ratings_df, watchlist_df)
    profile = build_affinity(ratings_df) if not ratings_df.empty else None
    if profile is not None and not profile.is_empty:
        screenings = attach_match(screenings, cache_df, profile)

    screenings = screenings.copy()
    screenings["_dedupe_key"] = screenings.apply(_dedupe_key, axis=1)

    # ── Only times I'm free ──────────────────────────────────────────────────
    # The page's one control — same widget and semantics as the Watchlist
    # Showtimes page, via ui.render_free_time_filter. Applied immediately,
    # before anything below is built, so the KPI strip and every curated
    # section reflect screenings the user can actually attend.
    screenings = render_free_time_filter(screenings, key_prefix="paris").apply(screenings)
    if screenings.empty:
        render_empty_state("🔍", "Nothing in your free time", "Loosen the free-time filter to see more screenings.")
        return

    # ── KPI strip ────────────────────────────────────────────────────────────
    def _n_unique(status: str) -> int:
        return int(screenings.loc[screenings["watch_status"] == status, "_dedupe_key"].nunique())

    render_kpi_strip(
        [
            ("Films screening", int(screenings["_dedupe_key"].nunique())),
            ("New to you", _n_unique("untracked")),
            ("On your watchlist", _n_unique("watchlist")),
            ("Already seen", _n_unique("seen")),
        ]
    )

    # ── Curated sections ─────────────────────────────────────────────────────
    # Each answers one question about the week's programme, and each is omitted
    # rather than rendered empty (the house rule — see the movie detail page).
    # All three are independent of each other, mirroring Home's "Top matches
    # this week": the answer leads — there is no browse-everything rail after.
    # The condition is repeated inline rather than reusing `has_profile`
    # because mypy narrows `profile` out of `TasteProfile | None` only on the
    # explicit `is not None` test, not through a boolean alias.
    has_profile = profile is not None and not profile.is_empty
    chips_fn = (lambda r: match_chips_html(r, profile)) if profile is not None and not profile.is_empty else None

    # Coerce once: user_rating arrives nullable (pd.NA for unrated films) and
    # `series >= x` on a nullable dtype yields NA, which pandas refuses as a
    # boolean mask. to_numeric → NaN → comparison → False is the safe form.
    rating = pd.to_numeric(screenings["user_rating"], errors="coerce")
    match = pd.to_numeric(screenings["match"], errors="coerce") if "match" in screenings.columns else None

    # Grouped once from the (post free-time-filter) full multi-row frame, so
    # every card's showtime badges list every remaining screening for that
    # film — not just the single representative row a rail dedupes down to.
    # A dict comprehension, not `dict(screenings.groupby(...))`: pandas 3.x's
    # GroupBy exposes a `.keys` attribute (the grouping column name, a plain
    # string) that shadows the mapping protocol `dict()` looks for, raising
    # "'str' object is not callable". Iterating sidesteps it.
    showtimes_by_key: dict[str, pd.DataFrame] = {k: g for k, g in screenings.groupby("_dedupe_key")}

    def _card_extra_html(row: pd.Series) -> str:
        chips = chips_fn(row) if chips_fn is not None else ""
        return chips + _showtime_badges_html(row["_dedupe_key"], showtimes_by_key)

    def _rail(rows: pd.DataFrame, *, title: str, sort_by: list[str], size: int = TOP_MATCHES_SIZE) -> None:
        """Dedupe to one card per film, order, cap, and render — or omit if empty."""
        if rows.empty:
            return
        top = rows.sort_values(sort_by, ascending=False, na_position="last")
        top = top.drop_duplicates(subset=["_dedupe_key"]).head(size)
        if not top.empty:
            render_poster_rail(top, title=title, extra_html_fn=_card_extra_html)

    if has_profile:
        # What's screening that you've never rated and never watchlisted.
        _rail(
            screenings[screenings["watch_status"] == "untracked"].dropna(subset=["match"]),
            title="Best matches — new to you",
            sort_by=["match"],
        )

    # The disagreement rail: you rated it below "good", but the taste ranker —
    # which knows your directors, genres, themes and cast — scores it highly
    # anyway. Needs the match column, since the whole premise is that value.
    if match is not None:
        _rail(
            screenings[((rating < RETRY_MAX_RATING) & (match >= RETRY_MIN_MATCH)).fillna(False)],
            title="Worth a second chance?",
            sort_by=["match"],
        )

    # Films you loved, back on a big screen. Ordered by your own rating first —
    # this rail is about your verdict, not the ranker's. Last and biggest: it
    # draws on films you've already vouched for, so there's more worth showing.
    _rail(
        screenings[(rating >= REWATCH_MIN_RATING).fillna(False)],
        title="Worth a rewatch!",
        sort_by=["user_rating", "letterboxd_avg_rating"],
        size=REWATCH_RAIL_SIZE,
    )


main()
