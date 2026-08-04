"""
Full-showtimes × Letterboxd-cache join for the "Screening in Paris" discovery page.

Every other surface in the dashboard is built on
:func:`sources.loader.build_watchlist_showtimes`, which inner-joins showtimes
against the *watchlist* only — so a film screening this week that the user
hasn't watchlisted never appears anywhere. Measured against the real parquets:
250 films screen across 13 Paris theaters in a week, and the watchlist-only
join surfaces 14 of them. :func:`build_screenings` joins the same showtimes
against the full metadata cache (``data_letterboxd.parquet``, a superset of
both the ratings and watchlist parquets) instead, and labels every row with a
:data:`WatchStatus` so the page can offer "what's screening that I haven't
seen" as a first-class filter rather than requiring a watchlist add first.

The join reuses :func:`sources.loader._normalize_title` and
:func:`sources.loader._directors_overlap` — the exact title-matched,
director-confirmed contract :func:`~sources.loader.build_watchlist_showtimes`
uses — so a film that would link to a watchlist screening links here too, and
like that function this is an **inner** join: a showtime whose title matches
nothing in the cache (or whose director doesn't confirm a title collision) is
dropped. A film with no cache row has no metadata to rank, no poster and no
detail page, so it can only ever render as a dead card here; the enrichment
step already reports that set separately in ``unresolved_allocine.parquet``,
which is where diagnosing a match failure belongs.

``user_rating`` is joined on from the ratings parquet, because the cache has no
such column and the page's "rewatch" / "second chance" sections are cuts on it.

Public API:
    WatchStatus                 Literal["untracked", "watchlist", "seen"]
    WATCH_STATUSES               the three statuses, in display-priority order
    WATCH_STATUS_LABELS          status -> human label
    build_screenings(...)        the labelled showtimes x cache join
"""

from __future__ import annotations

import logging
from typing import Literal, get_args

import pandas as pd
import streamlit as st

from sources.loader import DATA_TTL_SECONDS, _directors_overlap, _normalize_title

log = logging.getLogger(__name__)

WatchStatus = Literal["untracked", "watchlist", "seen"]

#: The three statuses in display-priority order — untracked (the discovery
#: case) first. Page sections and tests iterate this instead of hard-coding
#: the three strings.
WATCH_STATUSES: tuple[WatchStatus, ...] = get_args(WatchStatus)

#: Human labels for display. Kept beside WATCH_STATUSES so the two can't
#: drift out of sync.
WATCH_STATUS_LABELS: dict[WatchStatus, str] = {
    "untracked": "New to you",
    "watchlist": "Watchlist",
    "seen": "Seen",
}

# Cache metadata carried onto the joined frame. Mirrors
# sources.loader.build_watchlist_showtimes's _want_cols exactly (including the
# omissions — themes/mini_themes/cast/country/language/release_year are left
# for core.taste.attach_match to carry back in, same as it already does for
# the watchlist join) so a film's card renders identically whichever join
# produced the row.
_CACHE_WANT_COLS = [
    "slug",
    "title",
    "french_title",
    "runtime",
    "genres",
    "letterboxd_avg_rating",
    "directors",
    "release_year",
    "poster_url",
    "banner_url",
    "trailer_url",
    "tmdb_id",
]


def _match_cache(showtimes_df: pd.DataFrame, cache_df: pd.DataFrame) -> pd.DataFrame:
    """Inner-join showtimes against the Letterboxd cache, title-matched and director-confirmed.

    Same two-pass key (Allocine's ``movie`` against both the cache's
    ``french_title`` and ``title``) and the same director-overlap
    confirmation as :func:`sources.loader.build_watchlist_showtimes` — see
    that function's docstring for why both title forms are tried and why
    confirmation is token-subset containment, not exact equality. A showtime
    that never confirms a match is dropped, same as there.

    ``director`` (Allocine's raw column) is deliberately **not** dropped, even
    though it duplicates the cache's ``directors`` on every surviving row:
    :func:`ui.cards._directors_of` prefers ``directors`` and falls back to
    ``director``, so keeping both means a cache row with a blank ``directors``
    still renders a director sub-line instead of a blank one.
    """
    showtimes_df = showtimes_df.copy().reset_index(drop=True)
    showtimes_df["_st_idx"] = showtimes_df.index
    showtimes_df["_key"] = showtimes_df["movie"].map(_normalize_title)

    meta_cols = [c for c in _CACHE_WANT_COLS if c in cache_df.columns]
    meta = cache_df[meta_cols].copy()
    meta = meta.rename(columns={"runtime": "runtime_minutes", "slug": "letterboxd_slug", "title": "letterboxd_title"})
    meta = meta.reset_index(drop=True)
    meta["_meta_idx"] = meta.index

    key_cols = [c for c in ("french_title", "letterboxd_title") if c in meta.columns]
    if key_cols:
        meta_keyed = pd.concat([meta.assign(_key=meta[c].map(_normalize_title)) for c in key_cols], ignore_index=True)
        meta_keyed = meta_keyed[meta_keyed["_key"] != ""]
        meta_keyed = meta_keyed.drop_duplicates(subset=["_meta_idx", "_key"]).drop(columns=["_meta_idx"])
    else:
        # No candidate title column on the cache side (an empty/malformed
        # cache_df with no columns at all) — every showtime is unmatched by
        # construction; build a same-shaped, zero-row frame so the merge
        # below still runs instead of failing on an empty concat list.
        meta_keyed = meta.drop(columns=["_meta_idx"], errors="ignore").assign(_key="").iloc[0:0]

    pass1 = showtimes_df.merge(meta_keyed, on="_key", how="inner")
    if "director" in pass1.columns and "directors" in pass1.columns and not pass1.empty:
        pass1 = pass1[pass1.apply(lambda r: _directors_overlap(r["director"], r["directors"]), axis=1)]
    if "letterboxd_slug" in pass1.columns:
        pass1 = pass1.drop_duplicates(subset=["_st_idx", "letterboxd_slug"])
    else:
        pass1 = pass1.drop_duplicates(subset=["_st_idx"])

    # release_year is dropped (not merely de-collided from the merge's _x/_y
    # suffixes) so the column is absent from the joined frame entirely, rather
    # than present-with-holes — that is what lets core.taste.attach_match's
    # carry logic backfill it cleanly from cache_df for every row it scores
    # (see attach_match's docstring: it carries a metadata column back in only
    # when the joined frame doesn't already have it "with holes"; the same
    # pattern build_watchlist_showtimes itself relies on for
    # themes/mini_themes/cast/country/language).
    # "french_title" is dropped here too — the cache carries its own
    # french_title column (used only as a join key above), and renaming
    # showtimes' "movie" to "french_title" right after would otherwise
    # collide with it and produce two identically-named columns. Mirrors
    # build_watchlist_showtimes, which drops the same column for the same reason.
    _matched_drop = ("_key", "_st_idx", "original_title", "french_title", "release_year", "release_year_x", "release_year_y")
    drop_cols = [c for c in _matched_drop if c in pass1.columns]
    return pass1.drop(columns=drop_cols).rename(columns={"movie": "french_title"}).reset_index(drop=True)


def _watch_status(slug: object, seen_slugs: set[str], watchlist_slugs: set[str]) -> WatchStatus:
    """Classify one row's slug into a :data:`WatchStatus`.

    "seen" wins over "watchlist" (a rated film may also still be flagged on
    the watchlist on real data; having watched it is the more informative
    status). A ``NaN``/blank slug is ``"untracked"`` — it can't be looked up
    in either set, and after the inner join it only arises from a cache with
    no ``slug`` column at all.
    """
    if not isinstance(slug, str) or not slug:
        return "untracked"
    if slug in seen_slugs:
        return "seen"
    if slug in watchlist_slugs:
        return "watchlist"
    return "untracked"


@st.cache_data(ttl=DATA_TTL_SECONDS)
def build_screenings(
    showtimes_df: pd.DataFrame,
    cache_df: pd.DataFrame,
    ratings_df: pd.DataFrame,
    watchlist_df: pd.DataFrame,
) -> pd.DataFrame:
    """Join every showtime against the Letterboxd cache and label its watch status.

    ``showtimes_df`` should already be narrowed to upcoming rows (see
    :func:`sources.loader.future_showtimes`) — this function doesn't filter by
    time itself, mirroring ``build_watchlist_showtimes``. Returns one row per
    (showtime, matched film), carrying every ``build_watchlist_showtimes``-style
    column (``letterboxd_slug``, ``letterboxd_title``, ``runtime_minutes``, …)
    plus ``watch_status`` and ``user_rating``. A row's slug is looked up
    against ``ratings_df["slug"]`` (→ ``"seen"``) then ``watchlist_df["slug"]``
    (→ ``"watchlist"``), falling back to ``"untracked"``.

    ``user_rating`` is mapped on from ``ratings_df`` by slug (``NaN`` for
    anything unrated) because the metadata cache has no such column — the
    page's "worth a rewatch" / "second chance" sections are cuts on it, and
    ``core.movie.load_movie`` already joins it the same way for the detail page.

    Score the returned frame with ``core.taste.attach_match`` (passing the
    *same* ``cache_df`` as its metadata source) to get the 0–100 match badge —
    not done here, since scoring needs a ``TasteProfile`` the page derives once
    and this function is cached independently of it.
    """
    joined = _match_cache(showtimes_df, cache_df)
    seen_slugs = set(ratings_df["slug"].dropna()) if "slug" in ratings_df.columns else set()
    watchlist_slugs = set(watchlist_df["slug"].dropna()) if "slug" in watchlist_df.columns else set()

    slug_col = joined["letterboxd_slug"] if "letterboxd_slug" in joined.columns else pd.Series([None] * len(joined))
    joined["watch_status"] = [_watch_status(s, seen_slugs, watchlist_slugs) for s in slug_col]

    if {"slug", "user_rating"} <= set(ratings_df.columns) and "letterboxd_slug" in joined.columns:
        by_slug = ratings_df.dropna(subset=["slug"]).drop_duplicates(subset=["slug"]).set_index("slug")["user_rating"]
        joined["user_rating"] = joined["letterboxd_slug"].map(by_slug)
    else:
        joined["user_rating"] = pd.Series([pd.NA] * len(joined), dtype="Float64")

    log.info(
        "Paris screenings join: %d showtimes matched to the cache (%s)",
        len(joined),
        ", ".join(f"{s}={int((joined['watch_status'] == s).sum())}" for s in WATCH_STATUSES),
    )
    return joined
