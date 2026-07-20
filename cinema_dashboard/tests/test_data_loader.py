import pandas as pd
import pytest
from utils.data_loader import (
    _director_key,
    _directors_overlap,
    _normalize_title,
    attach_streaming,
    build_taste_profile,
    build_watchlist_showtimes,
    future_showtimes,
)

# ---------------------------------------------------------------------------
# build_taste_profile
# ---------------------------------------------------------------------------


def test_taste_profile_empty_df():
    assert build_taste_profile(pd.DataFrame()) == "No rating history available."


def test_taste_profile_missing_user_rating_col():
    df = pd.DataFrame({"genres": ["Drama"]})
    assert build_taste_profile(df) == "No rating history available."


def test_taste_profile_avg_only():
    df = pd.DataFrame({"user_rating": [3.0, 5.0]})
    result = build_taste_profile(df)
    assert result.startswith("Average rating given: 4.0")
    assert "Favourite genres" not in result
    assert "Favourite directors" not in result


def test_taste_profile_top_genres():
    df = pd.DataFrame(
        {
            "user_rating": [5.0, 4.0, 3.0],
            "genres": ["Drama, Action", "Drama", "Comedy"],
        }
    )
    result = build_taste_profile(df)
    assert "Favourite genres:" in result
    assert "Drama" in result


def test_taste_profile_top_directors_min2():
    df = pd.DataFrame(
        {
            "user_rating": [5.0, 4.0, 3.0],
            "directors": ["Alice, Bob", "Alice", "Bob"],
        }
    )
    result = build_taste_profile(df)
    assert "Favourite directors" in result
    assert "Alice" in result
    assert "Bob" in result


def test_taste_profile_director_with_single_film_excluded():
    df = pd.DataFrame(
        {
            "user_rating": [5.0, 4.0, 3.0],
            "directors": ["Alice", "Bob", "Charlie"],
        }
    )
    result = build_taste_profile(df)
    assert "Favourite directors" not in result


# ---------------------------------------------------------------------------
# future_showtimes
# ---------------------------------------------------------------------------


def test_future_showtimes_filters_past():
    df = pd.DataFrame({"showtimes": [pd.Timestamp("2000-01-01"), pd.Timestamp("2099-01-01")]})
    result = future_showtimes(df)
    assert len(result) == 1
    assert result.iloc[0]["showtimes"] == pd.Timestamp("2099-01-01")


def test_future_showtimes_keeps_future():
    df = pd.DataFrame({"showtimes": [pd.Timestamp("2099-06-01"), pd.Timestamp("2099-07-01")]})
    result = future_showtimes(df)
    assert len(result) == 2


def test_future_showtimes_empty_input():
    df = pd.DataFrame({"showtimes": pd.Series([], dtype="datetime64[ns]")})
    result = future_showtimes(df)
    assert result.empty


def test_future_showtimes_anchors_now_to_paris(mocker):
    # Freeze "now" to a fixed Paris instant; the naive column straddles it.
    mocker.patch("utils.data_loader._now_paris", return_value=pd.Timestamp("2030-06-01 20:00", tz="Europe/Paris"))
    df = pd.DataFrame({"showtimes": [pd.Timestamp("2030-06-01 19:59"), pd.Timestamp("2030-06-01 20:01")]})
    result = future_showtimes(df)
    assert len(result) == 1
    assert result.iloc[0]["showtimes"] == pd.Timestamp("2030-06-01 20:01")


def test_future_showtimes_tz_aware_column():
    # A tz-aware column must not raise "Cannot compare tz-naive and tz-aware".
    df = pd.DataFrame(
        {"showtimes": [pd.Timestamp("2000-01-01", tz="Europe/Paris"), pd.Timestamp("2099-01-01", tz="Europe/Paris")]}
    )
    result = future_showtimes(df)
    assert len(result) == 1
    assert result.iloc[0]["showtimes"] == pd.Timestamp("2099-01-01", tz="Europe/Paris")


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — basic title match
# ---------------------------------------------------------------------------


def test_exact_match(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Dune"


def test_case_insensitive_match(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "DUNE", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_no_match_returns_empty(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Oppenheimer"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_movie_used_when_original_title_absent(make_showtimes, make_watchlist):
    # No original_title in showtimes — movie is the primary key
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_runtime_column_renamed(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "runtime": 155}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "runtime_minutes" in result.columns
    assert "runtime" not in result.columns


def test_runtime_from_watchlist_not_scraper(make_showtimes, make_watchlist):
    # Both sources have a runtime column; watchlist value (155) must win
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00", "runtime": 999}])
    watchlist = make_watchlist([{"title": "Dune", "runtime": 155}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.iloc[0]["runtime_minutes"] == 155


def test_slug_column_dropped(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "slug": "dune-2021"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "letterboxd_slug" not in result.columns
    assert "slug" not in result.columns


def test_multiple_showtimes_for_same_movie(make_showtimes, make_watchlist):
    showtimes = make_showtimes(
        [
            {"movie": "Dune", "showtimes": "2025-01-01 14:00"},
            {"movie": "Dune", "showtimes": "2025-01-01 20:00"},
        ]
    )
    watchlist = make_watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 2


def test_key_column_not_in_output(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "_key" not in result.columns


def test_trailer_url_carried_onto_joined_rows(make_showtimes, make_watchlist):
    # trailer_url (Phase 1.1 upstream cache column) must survive the join so
    # Home/Calendar cards can render the trailer chip (see utils/ui.py).
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "trailer_url": "https://www.youtube.com/watch?v=abc123"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert "trailer_url" in result.columns
    assert result.iloc[0]["trailer_url"] == "https://www.youtube.com/watch?v=abc123"


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — director-aware merge
# ---------------------------------------------------------------------------


def test_director_match_single(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Obsession", "director": "Brian De Palma", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Obsession", "directors": "Brian De Palma"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_match_multiple(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "No Country", "director": "Joel Coen | Ethan Coen", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "No Country", "directors": "Ethan Coen, Joel Coen"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_partial_overlap_kept(make_showtimes, make_watchlist):
    # Allocine and Letterboxd disagree on one co-director but share one → keep
    showtimes = make_showtimes(
        [{"movie": "The Kid Brother", "director": "Harold Lloyd | Lewis Milestone", "showtimes": "2025-01-01 18:00"}]
    )
    watchlist = make_watchlist([{"title": "The Kid Brother", "directors": "Ted Wilde, Harold Lloyd"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_no_overlap_filtered_out(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Obsession", "director": "Brian De Palma", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Obsession", "directors": "Edward Dmytryk"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_director_missing_one_side_falls_back_to_title(make_showtimes, make_watchlist):
    # showtimes has no director column at all → director filter is skipped
    # entirely, title-only match, should still return 1 row. (Per-row NaN is a
    # different case — see test_blank_allocine_director_does_not_attach.)
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_case_and_accent_normalised(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Nikita", "director": "luc besson", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Nikita", "directors": "Luc Besson"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — title normalisation
# ---------------------------------------------------------------------------


def test_accent_normalised_in_title_key(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Détective", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Detective"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Détective"


def test_punctuation_normalised_in_title_key(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Spider-Man: No Way Home", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Spider Man No Way Home"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_original_title_matched_on_both_sides(make_showtimes, make_watchlist):
    # Allocine movie is accented; watchlist title is unaccented — normalization bridges them
    showtimes = make_showtimes([{"movie": "Détective", "original_title": "Détective", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Detective", "original_title": "Détective"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_french_title_matches_allocine_movie(make_showtimes, make_watchlist):
    # Pass 1: Allocine movie (FR display title) vs TMDB french_title on watchlist.
    # Letterboxd title is English — without french_title, Pass 1 would miss.
    showtimes = make_showtimes([{"movie": "Les Quatre Cents Coups", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "The 400 Blows", "french_title": "Les Quatre Cents Coups"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Les Quatre Cents Coups"


def test_original_title_matches_despite_french_retitle(make_showtimes, make_watchlist):
    # Repertory screenings often run under the original title (VO) even when
    # TMDB carries a French retitle. Regression: "Sudden Fear" screened as such
    # at Reflet Medicis while the watchlist held "Le Masque arraché" — the old
    # french_title-with-fallback key never tried the original title.
    showtimes = make_showtimes([{"movie": "Sudden Fear", "director": "David Miller", "showtimes": "2026-07-15 12:45"}])
    watchlist = make_watchlist([{"title": "Sudden Fear", "french_title": "Le Masque arraché", "directors": "David Miller"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1
    assert result.iloc[0]["french_title"] == "Sudden Fear"


def test_original_title_match_still_requires_director_confirmation(make_showtimes, make_watchlist):
    # The wider key net must not weaken precision: an original-title collision
    # with a different director is still rejected.
    showtimes = make_showtimes([{"movie": "Sudden Fear", "director": "Someone Else", "showtimes": "2026-07-15 12:45"}])
    watchlist = make_watchlist([{"title": "Sudden Fear", "french_title": "Le Masque arraché", "directors": "David Miller"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_no_duplicate_when_french_and_original_titles_equal(make_showtimes, make_watchlist):
    # Both candidate titles normalise to the same key; the keyed watchlist
    # frame is deduplicated so the single showtime matches exactly once.
    showtimes = make_showtimes([{"movie": "Dune", "director": "Denis Villeneuve", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "french_title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_remake_disambiguated_by_director(make_showtimes, make_watchlist):
    # Two films share the same French title. Director filter keeps only the correct match.
    showtimes = make_showtimes(
        [
            {
                "movie": "Solaris",
                "director": "Andrei Tarkovsky",
                "showtimes": "2025-01-01 18:00",
            },
            {
                "movie": "Solaris",
                "director": "Steven Soderbergh",
                "showtimes": "2025-01-02 18:00",
            },
        ]
    )
    watchlist = make_watchlist(
        [
            {"slug": "solaris-1972", "title": "Solaris", "directors": "Andrei Tarkovsky", "release_year": 1972},
            {"slug": "solaris-2002", "title": "Solaris", "directors": "Steven Soderbergh", "release_year": 2002},
        ]
    )
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 2
    pairs = sorted(zip(result["french_title"].tolist(), result["directors"].tolist()))
    assert pairs == [("Solaris", "Andrei Tarkovsky"), ("Solaris", "Steven Soderbergh")]


def test_no_duplicate_when_both_passes_could_match(make_showtimes, make_watchlist):
    # Pass 1 hits on title; Pass 2 is skipped (row already matched). Exactly 1 row.
    showtimes = make_showtimes(
        [
            {
                "movie": "Dune",
                "director": "Denis Villeneuve",
                "release_year": 2021,
                "showtimes": "2025-01-01 18:00",
            }
        ]
    )
    watchlist = make_watchlist([{"title": "Dune", "directors": "Denis Villeneuve", "release_year": 2021}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — no-match guards
# ---------------------------------------------------------------------------


def test_no_match_when_title_absent_from_watchlist(make_showtimes, make_watchlist):
    # Showtime title not on watchlist → no match, even if director matches.
    showtimes = make_showtimes([{"movie": "Film X", "director": "Wong Kar-wai", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist(
        [
            {"title": "In the Mood for Love", "french_title": "In the Mood for Love", "directors": "Wong Kar-wai"},
        ]
    )
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_no_match_when_title_absent_and_director_absent(make_showtimes, make_watchlist):
    # No director column on showtimes, title doesn't match → no match.
    showtimes = make_showtimes([{"movie": "Unknown Film", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Known Film", "french_title": "Titre Différent", "directors": "Some Director"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_no_match_when_director_keys_differ(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Film A", "director": "Jean-Luc Godard", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Film B", "french_title": "Film C", "directors": "François Truffaut"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_no_match_when_title_and_director_both_differ(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "X", "director": "Wrong Director", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Y", "french_title": "Titre Z", "directors": "Right Director"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — dedup + wrong-attach guard
# ---------------------------------------------------------------------------


def test_dedup_same_showtime_same_slug():
    # One showtime matching two watchlist rows with the same slug produces a
    # duplicate (_st_idx × letterboxd_slug identical). The dedup branch should
    # collapse it to a single row.
    showtimes = pd.DataFrame(
        {
            "movie": ["Parasite"],
            "showtimes": [pd.Timestamp("2099-01-01 20:00")],
            "theater_name": ["Cinema A"],
        }
    )
    watchlist = pd.DataFrame(
        {
            "slug": ["parasite", "parasite"],
            "title": ["Parasite", "Parasite"],
            "runtime": [132, 132],
            "genres": ["Drama, Thriller", "Drama, Thriller"],
            "directors": ["Bong Joon-ho", "Bong Joon-ho"],
        }
    )
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_blank_watchlist_director_does_not_attach_wrong_film(make_showtimes, make_watchlist):
    # A recurring French title ("Nosferatu") where the watchlist entry has no
    # director must NOT pick up the showtime's (different) film. Precision-first:
    # without a confirmed director overlap the title collision is rejected.
    showtimes = make_showtimes(
        [
            {
                "movie": "Nosferatu",
                "director": "Robert Eggers",
                "showtimes": pd.Timestamp("2099-01-01 20:00"),
            }
        ]
    )
    watchlist = make_watchlist([{"slug": "nosferatu-1922", "title": "Nosferatu", "directors": float("nan")}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_blank_allocine_director_does_not_attach(make_showtimes, make_watchlist):
    # Mirror case: the showtime's director column exists but is NaN for this row.
    # On real data Allocine omits the director for ~0.6% of films; an unconfirmed
    # title collision must not attach.
    showtimes = make_showtimes([{"movie": "Dune", "director": float("nan"), "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert result.empty


def test_matching_director_still_attaches(make_showtimes, make_watchlist):
    # Sanity floor: a confirmed director overlap on the same title is kept.
    showtimes = make_showtimes(
        [{"movie": "Nosferatu", "director": "Robert Eggers", "showtimes": pd.Timestamp("2099-01-01 20:00")}]
    )
    watchlist = make_watchlist([{"slug": "nosferatu-2024", "title": "Nosferatu", "directors": "Robert Eggers"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# _directors_overlap — precision-first confirmation
# ---------------------------------------------------------------------------


def test_directors_overlap_both_present_shared():
    assert _directors_overlap("Bong Joon-ho", "Bong Joon-ho") is True


def test_directors_overlap_normalises_order_and_accents():
    # NFKD + token sort: "Joon Ho Bong" and "Bóng Joon-ho" collapse to one key.
    assert _directors_overlap("Joon Ho Bong", "Bóng Joon-ho") is True


def test_directors_overlap_multi_director_one_shared():
    # Allocine uses " | ", Letterboxd uses ", "; one common name is enough.
    assert _directors_overlap("Jean Renoir | Alice Guy", "Alice Guy, Georges Méliès") is True


def test_directors_overlap_both_present_conflict():
    assert _directors_overlap("F.W. Murnau", "Robert Eggers") is False


def test_directors_overlap_allocine_blank_rejected():
    # Was True under the old null fallthrough; now rejected so a wrong film's
    # screening can't attach on a title-only collision.
    assert _directors_overlap(float("nan"), "Robert Eggers") is False


def test_directors_overlap_letterboxd_blank_rejected():
    assert _directors_overlap("Robert Eggers", float("nan")) is False


def test_directors_overlap_both_blank_rejected():
    assert _directors_overlap(float("nan"), float("nan")) is False


def test_directors_overlap_empty_string_rejected():
    # Non-NaN but whitespace-only → empty key set → no positive confirmation.
    assert _directors_overlap("Robert Eggers", "   ") is False


def test_directors_overlap_disambiguator_suffix():
    # Allocine appends a "(II)" disambiguator that TMDB omits: token
    # containment still confirms the match (regression: "Plus fort que moi").
    assert _directors_overlap("Kirk Jones (II)", "Kirk Jones") is True


def test_directors_overlap_generational_suffix():
    # "Jr." on one side only must not sink the match (regression:
    # "Un jour avec mon père").
    assert _directors_overlap("Akinola Davies", "Akinola Davies Jr.") is True


def test_directors_overlap_extra_name_tokens():
    # A fuller romanised name on one side is a superset of the shorter form
    # (regression: "City on fire").
    assert _directors_overlap("Ringo Lam", "Ringo Lam Ling-Tung") is True


def test_directors_overlap_disjoint_names_still_rejected():
    # Containment must not leak into a wrong-attach: genuinely different
    # directors on a title collision share no token-subset relationship.
    assert _directors_overlap("Steven Spielberg", "Byron Haskin") is False
    assert _directors_overlap("Mark Jenkin", "Hugo Haas") is False


# ---------------------------------------------------------------------------
# _director_key
# ---------------------------------------------------------------------------


def test_director_key_name_order_swap():
    assert _director_key("Bong Joon-ho") == _director_key("Joon Ho Bong")


def test_director_key_accent():
    assert _director_key("René Allio") == _director_key("Rene Allio")


def test_director_key_empty():
    assert _director_key("") == ""


def test_director_key_hyphen_removed():
    assert _director_key("Jean-Luc Godard") == _director_key("Jean Luc Godard")


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", float("nan")])
def test_normalize_title_null_variants(raw):
    assert _normalize_title(raw) == ""


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Détective", "Detective"),
        ("Wall-E", "Wall E"),
        ("Spider-Man: No Way Home", "Spider Man No Way Home"),
        ("2001: A Space Odyssey", "2001 A Space Odyssey"),
    ],
)
def test_normalize_title_equivalences(a, b):
    assert _normalize_title(a) == _normalize_title(b)


def test_normalize_title_preserves_digits():
    assert "2049" in _normalize_title("Blade Runner 2049")


# ---------------------------------------------------------------------------
# attach_streaming
# ---------------------------------------------------------------------------


def test_attach_streaming_no_tmdb_id_column(mocker):
    """Input without ``tmdb_id`` gets empty list columns for both channels, no merge attempted."""
    df = pd.DataFrame({"title": ["A"]})
    load = mocker.patch("utils.data_loader.load_streaming_providers")
    out = attach_streaming(df, "/tmp/movies")
    load.assert_not_called()
    assert out["flatrate"].tolist() == [[]]
    assert out["free"].tolist() == [[]]


def test_attach_streaming_empty_cache_returns_empty_lists(mocker):
    df = pd.DataFrame({"tmdb_id": ["1", "2"], "title": ["A", "B"]})
    mocker.patch(
        "utils.data_loader.load_streaming_providers",
        return_value=pd.DataFrame(columns=["tmdb_id", "flatrate", "free", "tmdb_link", "fetched_at"]),
    )
    out = attach_streaming(df, "/tmp/movies")
    assert len(out) == 2
    assert all(v == [] for v in out["flatrate"])
    assert all(v == [] for v in out["free"])


def test_attach_streaming_left_join_preserves_unmatched(mocker):
    df = pd.DataFrame({"tmdb_id": ["1", "2", "3"], "title": ["A", "B", "C"]})
    cache = pd.DataFrame(
        {
            "tmdb_id": ["1", "3"],
            "flatrate": [["mubi"], ["netflix", "canalplus"]],
            "free": [[], ["arte"]],
            "tmdb_link": ["", ""],
            "fetched_at": [pd.Timestamp.now("UTC"), pd.Timestamp.now("UTC")],
        }
    )
    mocker.patch("utils.data_loader.load_streaming_providers", return_value=cache)
    out = attach_streaming(df, "/tmp/movies").set_index("tmdb_id")
    assert out.loc["1", "flatrate"] == ["mubi"]
    assert out.loc["1", "free"] == []
    assert out.loc["2", "flatrate"] == []  # unmatched → empty list, not NaN
    assert out.loc["2", "free"] == []
    assert out.loc["3", "flatrate"] == ["netflix", "canalplus"]
    assert out.loc["3", "free"] == ["arte"]
