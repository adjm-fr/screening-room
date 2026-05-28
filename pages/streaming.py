"""
Streaming — watchlist films currently available on subscription streaming (France).

One horizontal poster rail per provider. When ``STREAMING_SERVICES`` is set,
rails are limited to those subscribed providers; otherwise every provider
returned by TMDB for the watchlist gets a rail. The chip filter at the top
operates on display names (``Canal+``, ``MUBI``…), not raw slugs.
"""

from __future__ import annotations

import streamlit as st

from modules.config import settings
from utils.data_loader import attach_streaming, get_paths, load_watchlist
from utils.streaming import display_name, load_display_names_catalog
from utils.ui import render_chip_filter, render_empty_state, render_poster_rail


def main() -> None:
    movies_path, _, _ = get_paths()

    st.markdown(
        '<h1 class="h-display" style="font-size:2.4rem;">'
        "Streaming "
        '<span class="chip" style="font-family:Inter,sans-serif;vertical-align:middle;'
        'font-size:0.75rem;margin-left:0.5rem;">🇫🇷 France</span>'
        "</h1>",
        unsafe_allow_html=True,
    )
    st.caption("Watchlist films currently available on subscription streaming in France. Source: TMDB FR.")

    if not movies_path:
        render_empty_state(
            "⚙️",
            "Configure your data paths",
            "Set MOVIES_OUTPUT_PATH in .env to populate the dashboard.",
        )
        return
    if not (movies_path / "watchlist_with_letterboxd.parquet").exists():
        render_empty_state(
            "🎬",
            "No watchlist yet",
            "Run the orchestrate.py CLI (or Dagster) to scrape the watchlist.",
        )
        return

    try:
        watchlist_df = load_watchlist(str(movies_path))
    except Exception as exc:
        st.error(f"Failed to load watchlist: {exc}")
        return

    # The movie-card renderer looks for `letterboxd_title` first; mirror the
    # rename used by `build_watchlist_showtimes` so cards display the canonical
    # Letterboxd title rather than the French one.
    if "title" in watchlist_df.columns and "letterboxd_title" not in watchlist_df.columns:
        watchlist_df = watchlist_df.rename(columns={"title": "letterboxd_title"})

    df = attach_streaming(watchlist_df, str(movies_path))
    df = df[df["flatrate"].apply(lambda f: len(f) > 0)]

    if df.empty:
        render_empty_state(
            "📺",
            "Streaming cache is empty",
            "Set TMDB_API_KEY in .env and run `uv run python orchestrate.py` to populate it.",
        )
        return

    subscribed = settings.streaming_service_slugs
    providers_present: set[str] = set()
    for flat in df["flatrate"]:
        providers_present.update(flat)

    providers_to_show = (subscribed & providers_present) if subscribed else providers_present

    if not providers_to_show:
        render_empty_state(
            "📺",
            "No matching streaming providers",
            "None of your subscribed services currently carry watchlist films in France.",
        )
        return

    # Display-name <-> slug bridge so the chip filter shows human names
    # (sourced from the on-disk catalogue grown by `refresh_streaming_providers`)
    # while filtering still happens on slugs (the canonical join key).
    catalogue = load_display_names_catalog()
    display_to_slug: dict[str, str] = {display_name(s, catalogue): s for s in providers_to_show}
    _ALL = "All"
    display_options = [_ALL, *sorted(display_to_slug.keys(), key=str.lower)]

    # Multi-select with a mutually-exclusive "All" sentinel: picking a real
    # provider while "All" is active drops "All"; picking "All" clears any
    # other selection; deselecting the last chip falls back to "All".
    _KEY = "streaming_platforms"
    _PREV = "streaming_platforms_prev"

    def _normalize_all_sentinel() -> None:
        picked = list(st.session_state.get(_KEY) or [])
        prev = st.session_state.get(_PREV, [_ALL])
        if not picked:
            new = [_ALL]
        elif _ALL in picked and _ALL not in prev:
            new = [_ALL]
        elif _ALL in picked and len(picked) > 1:
            new = [p for p in picked if p != _ALL]
        else:
            new = picked
        st.session_state[_KEY] = new
        st.session_state[_PREV] = new

    picked = render_chip_filter(
        "Platforms",
        display_options,
        key=_KEY,
        selection_mode="multi",
        default=[_ALL],
        on_change=_normalize_all_sentinel,
    )
    st.session_state[_PREV] = picked

    if _ALL in picked:
        selected_slugs = set(display_to_slug.values())
    else:
        selected_slugs = {display_to_slug[d] for d in picked if d in display_to_slug}

    for slug in sorted(selected_slugs, key=lambda s: display_name(s, catalogue).lower()):
        rows = df[df["flatrate"].apply(lambda flat, s=slug: s in flat)]
        if rows.empty:
            continue
        rows = (
            rows.sort_values("letterboxd_avg_rating", ascending=False, na_position="last")
            .drop_duplicates(subset=["tmdb_id"])
            .head(20)
        )
        render_poster_rail(rows, title=display_name(slug, catalogue), subscribed=subscribed)


main()
