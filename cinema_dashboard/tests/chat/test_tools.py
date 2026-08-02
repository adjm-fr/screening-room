"""Tests for chat.tools — the pure ``top_matches`` / ``showtimes_query`` handlers.

Both handlers are Streamlit-free filters over the injected showtimes frame, so
everything here is plain pandas: no session, no network, no Gemini. The
closed-set invariant (results are always rows of the passed frame) and the
never-raise contract are the two properties worth pinning.
"""

from __future__ import annotations

import pandas as pd
import pytest

from chat.tools import MAX_SHOWTIME_ROWS, MAX_STREAMING_ROWS, MAX_TOP_MATCHES, showtimes_query, streaming_query, top_matches


@pytest.fixture
def scored():
    """Three films, one of them screening twice (as ``wl_scored`` really is)."""
    return pd.DataFrame(
        [
            {
                "letterboxd_title": "Ran",
                "french_title": "Ran",
                "genres": "Drama, War",
                "directors": "Akira Kurosawa",
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2026-07-25 20:00"),
                "match": 91.0,
            },
            {
                "letterboxd_title": "Ran",
                "french_title": "Ran",
                "genres": "Drama, War",
                "directors": "Akira Kurosawa",
                "theater_name": "Reflet Médicis",
                "showtimes": pd.Timestamp("2026-07-26 18:00"),
                "match": 91.0,
            },
            {
                "letterboxd_title": "Sudden Fear",
                "french_title": "Le Masque arraché",
                "genres": "Thriller",
                "directors": "David Miller",
                "theater_name": "MK2 Beaubourg",
                "showtimes": pd.Timestamp("2026-07-25 21:30"),
                "match": 72.0,
            },
            {
                "letterboxd_title": "Aftersun",
                "french_title": "Aftersun",
                "genres": "Drama",
                "directors": "Charlotte Wells",
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2026-07-27 19:00"),
                "match": 55.0,
            },
        ]
    )


# ── top_matches ──────────────────────────────────────────────────────────────


def test_top_matches_orders_by_match_descending(scored):
    assert [r["title"] for r in top_matches(scored)] == ["Ran", "Sudden Fear", "Aftersun"]


def test_top_matches_honours_n(scored):
    assert len(top_matches(scored, n=2)) == 2


def test_top_matches_caps_at_hard_limit(scored):
    # n is clamped to MAX_TOP_MATCHES; the frame here only has 3 distinct films.
    assert len(top_matches(scored, n=MAX_TOP_MATCHES + 500)) == 3


def test_top_matches_dedupes_repeated_showtime_rows(scored):
    titles = [r["title"] for r in top_matches(scored)]
    assert titles.count("Ran") == 1


def test_top_matches_keeps_the_soonest_screening_of_a_deduped_film(scored):
    ran = next(r for r in top_matches(scored) if r["title"] == "Ran")
    assert ran["showtime"] == "2026-07-25 20:00"
    assert ran["theater"] == "Le Champo"


def test_top_matches_filters_by_genre_case_insensitively(scored):
    assert [r["title"] for r in top_matches(scored, genre="drama")] == ["Ran", "Aftersun"]


def test_top_matches_genre_without_genres_column_returns_nothing(scored):
    # Unfiltered rows would be presented by the model as genre matches.
    assert top_matches(scored.drop(columns=["genres"]), genre="Drama") == []


def test_top_matches_returns_rows_only_from_the_passed_frame(scored):
    known = set(scored["letterboxd_title"])
    assert {r["title"] for r in top_matches(scored)} <= known


def test_top_matches_empty_frame(scored):
    assert top_matches(scored.iloc[0:0]) == []


def test_top_matches_missing_match_column(scored):
    rows = top_matches(scored.drop(columns=["match"]))
    assert [r["title"] for r in rows] == ["Ran", "Sudden Fear", "Aftersun"]  # falls back to showtime order
    assert all("match" not in r for r in rows)


def test_top_matches_nan_match_sorts_last(scored):
    df = scored.copy()
    df.loc[df["letterboxd_title"] == "Ran", "match"] = float("nan")
    rows = top_matches(df)
    assert [r["title"] for r in rows] == ["Sudden Fear", "Aftersun", "Ran"]
    assert "match" not in rows[-1]  # NaN is dropped rather than serialized


def test_top_matches_survives_missing_title_columns(scored):
    rows = top_matches(scored.drop(columns=["letterboxd_title", "french_title"]))
    assert all(r["title"] is None for r in rows)


def test_top_matches_bad_n_falls_back_to_default(scored):
    assert len(top_matches(scored, n="lots")) == 3  # 3 distinct films, default cap is 5


# ── showtimes_query ──────────────────────────────────────────────────────────


def test_showtimes_query_no_filters_returns_everything_soonest_first(scored):
    rows = showtimes_query(scored)
    assert len(rows) == 4
    assert [r["showtime"] for r in rows] == sorted(r["showtime"] for r in rows)


def test_showtimes_query_filters_by_title(scored):
    assert [r["title"] for r in showtimes_query(scored, title="ran")] == ["Ran", "Ran"]


def test_showtimes_query_matches_the_french_title_too(scored):
    # Accent- and case-insensitive, via data_loader._normalize_title.
    assert [r["title"] for r in showtimes_query(scored, title="masque arrache")] == ["Sudden Fear"]


def test_showtimes_query_filters_by_theater(scored):
    assert {r["title"] for r in showtimes_query(scored, theater="champo")} == {"Ran", "Aftersun"}


def test_showtimes_query_filters_by_day(scored):
    rows = showtimes_query(scored, day="2026-07-25")
    assert {r["title"] for r in rows} == {"Ran", "Sudden Fear"}


def test_showtimes_query_combines_filters(scored):
    rows = showtimes_query(scored, title="Ran", theater="Champo", day="2026-07-25")
    assert [r["theater"] for r in rows] == ["Le Champo"]


def test_showtimes_query_unparseable_day_drops_the_day_filter(scored):
    # "tonight" can't be resolved here; returning nothing would read as "no
    # screenings that day", so the filter is ignored and every row states its date.
    rows = showtimes_query(scored, day="tonight")
    assert len(rows) == 4
    assert all(r["showtime"] for r in rows)


def test_showtimes_query_empty_frame(scored):
    assert showtimes_query(scored.iloc[0:0], title="Ran") == []


def test_showtimes_query_no_match_returns_empty(scored):
    assert showtimes_query(scored, title="Oppenheimer") == []


def test_showtimes_query_missing_theater_column_returns_nothing(scored):
    assert showtimes_query(scored.drop(columns=["theater_name"]), theater="Champo") == []


def test_showtimes_query_missing_title_columns_returns_nothing(scored):
    assert showtimes_query(scored.drop(columns=["letterboxd_title", "french_title"]), title="Ran") == []


def test_showtimes_query_missing_showtimes_column_with_day_returns_nothing(scored):
    assert showtimes_query(scored.drop(columns=["showtimes"]), day="2026-07-25") == []


def test_showtimes_query_tolerates_nan_cells(scored):
    df = scored.copy()
    df.loc[0, "theater_name"] = None
    df.loc[0, "match"] = float("nan")
    rows = showtimes_query(df, title="Ran")
    assert len(rows) == 2
    assert "theater" not in rows[0]


def test_showtimes_query_caps_result_size():
    df = pd.DataFrame(
        [
            {
                "letterboxd_title": "Ran",
                "theater_name": "Le Champo",
                "showtimes": pd.Timestamp("2026-07-25 20:00") + pd.Timedelta(hours=i),
            }
            for i in range(MAX_SHOWTIME_ROWS + 10)
        ]
    )
    assert len(showtimes_query(df)) == MAX_SHOWTIME_ROWS


def test_handlers_never_raise_on_a_malformed_frame():
    # ``showtimes`` holding un-sortable junk must degrade to [] , not explode.
    df = pd.DataFrame({"letterboxd_title": ["Ran"], "showtimes": [object()], "match": ["not a number"]})
    assert isinstance(top_matches(df), list)
    assert isinstance(showtimes_query(df, day="2026-07-25"), list)


# ── streaming_query ──────────────────────────────────────────────────────────


@pytest.fixture
def streaming():
    """Four watchlist films, mirroring the frame ``chat.prompt._streaming_context`` reads."""
    return pd.DataFrame(
        [
            {
                "letterboxd_title": "Perfect Days",
                "french_title": "Perfect Days",
                "flatrate": ["mubi"],
                "free": [],
            },
            {
                "letterboxd_title": "Aftersun",
                "french_title": "Aftersun",
                "flatrate": ["mubi", "netflix"],
                "free": ["arte"],
            },
            {
                "letterboxd_title": "Sudden Fear",
                "french_title": "Le Masque arraché",
                "flatrate": [],
                "free": [],
            },
            {
                "letterboxd_title": "Past Lives",
                "french_title": "Past Lives",
                "flatrate": [],
                "free": ["france.tv"],
            },
            {
                "letterboxd_title": "The Zone of Interest",
                "french_title": "La Zone d'intérêt",
                "flatrate": ["netflix"],
                "free": [],
            },
        ]
    )


def test_streaming_query_no_filters_returns_every_film_with_availability(streaming):
    titles = {r["title"] for r in streaming_query(streaming)}
    # Sudden Fear has neither list populated, so it's excluded.
    assert titles == {"Perfect Days", "Aftersun", "Past Lives", "The Zone of Interest"}


def test_streaming_query_filters_by_title(streaming):
    assert [r["title"] for r in streaming_query(streaming, title="perfect")] == ["Perfect Days"]


def test_streaming_query_matches_the_french_title_too(streaming):
    # Accent- and case-insensitive, via sources.loader._normalize_title.
    assert [r["title"] for r in streaming_query(streaming, title="zone d interet")] == ["The Zone of Interest"]


def test_streaming_query_title_match_without_streaming_is_still_excluded(streaming):
    # Sudden Fear's French title matches, but it has neither flatrate nor free.
    assert streaming_query(streaming, title="masque arrache") == []


def test_streaming_query_filters_by_provider_case_insensitively(streaming):
    assert [r["title"] for r in streaming_query(streaming, provider="MUBI")] == ["Perfect Days", "Aftersun"]


def test_streaming_query_filters_by_free_provider(streaming):
    assert [r["title"] for r in streaming_query(streaming, provider="france")] == ["Past Lives"]


def test_streaming_query_combines_title_and_provider(streaming):
    assert [r["title"] for r in streaming_query(streaming, title="after", provider="netflix")] == ["Aftersun"]


def test_streaming_query_entry_shape_omits_empty_lists(streaming):
    rows = {r["title"]: r for r in streaming_query(streaming)}
    assert rows["Perfect Days"] == {"title": "Perfect Days", "flatrate": ["mubi"]}
    assert rows["Aftersun"] == {"title": "Aftersun", "flatrate": ["mubi", "netflix"], "free": ["arte"]}
    assert rows["Past Lives"] == {"title": "Past Lives", "free": ["france.tv"]}


def test_streaming_query_no_provider_match_returns_empty(streaming):
    assert streaming_query(streaming, provider="disney+") == []


def test_streaming_query_no_title_match_returns_empty(streaming):
    assert streaming_query(streaming, title="Oppenheimer") == []


def test_streaming_query_empty_frame():
    assert streaming_query(pd.DataFrame(), title="Ran") == []


def test_streaming_query_none_frame():
    assert streaming_query(None) == []


def test_streaming_query_missing_flatrate_column_returns_nothing():
    assert streaming_query(pd.DataFrame({"letterboxd_title": ["Ran"]})) == []


def test_streaming_query_missing_title_columns_with_title_filter_returns_nothing(streaming):
    assert streaming_query(streaming.drop(columns=["letterboxd_title", "french_title"]), title="Ran") == []


def test_streaming_query_junk_provider_arg_is_ignored_not_raised(streaming):
    # A non-string provider (e.g. the model passing a list) must degrade gracefully.
    rows = streaming_query(streaming, provider=["mubi"])  # type: ignore[arg-type]
    assert isinstance(rows, list)


def test_streaming_query_closed_set_containment(streaming):
    # Every returned row is drawn from the passed frame — never a film outside it.
    known = set(streaming["letterboxd_title"])
    assert {r["title"] for r in streaming_query(streaming)} <= known


def test_streaming_query_caps_result_size():
    df = pd.DataFrame(
        [{"letterboxd_title": f"Film {i}", "flatrate": ["mubi"], "free": []} for i in range(MAX_STREAMING_ROWS + 10)]
    )
    assert len(streaming_query(df)) == MAX_STREAMING_ROWS


def test_streaming_query_dedupes_by_title():
    df = pd.DataFrame(
        [
            {"letterboxd_title": "Same", "flatrate": ["mubi"], "free": []},
            {"letterboxd_title": "Same", "flatrate": ["mubi"], "free": []},
        ]
    )
    assert len(streaming_query(df)) == 1


def test_streaming_query_never_raises_on_a_malformed_frame():
    df = pd.DataFrame({"letterboxd_title": ["Ran"], "flatrate": [object()], "free": [object()]})
    assert streaming_query(df) == []
