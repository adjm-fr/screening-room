"""Tests for the watchlist↔showtimes join and title/director normalisation."""

import pytest

from utils.data_loader import _director_key, _normalize_title, build_watchlist_showtimes

# ── build_watchlist_showtimes ─────────────────────────────────────────────────


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


# ── director-aware merge ──────────────────────────────────────────────────────


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
    # showtimes has no director column → title-only match, should still return 1 row
    showtimes = make_showtimes([{"movie": "Dune", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_nan_value_keeps_title_match(make_showtimes, make_watchlist):
    # director column exists but value is NaN for this row → should not filter out the match
    showtimes = make_showtimes([{"movie": "Dune", "director": float("nan"), "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Dune", "directors": "Denis Villeneuve"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


def test_director_case_and_accent_normalised(make_showtimes, make_watchlist):
    showtimes = make_showtimes([{"movie": "Nikita", "director": "luc besson", "showtimes": "2025-01-01 18:00"}])
    watchlist = make_watchlist([{"title": "Nikita", "directors": "Luc Besson"}])
    result = build_watchlist_showtimes(showtimes, watchlist)
    assert len(result) == 1


# ── title normalisation ───────────────────────────────────────────────────────


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


# ── Pass 2: director canonical key ───────────────────────────────────────────


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


# ── _director_key ─────────────────────────────────────────────────────────────


def test_director_key_name_order_swap():
    assert _director_key("Bong Joon-ho") == _director_key("Joon Ho Bong")


def test_director_key_accent():
    assert _director_key("René Allio") == _director_key("Rene Allio")


def test_director_key_empty():
    assert _director_key("") == ""


def test_director_key_hyphen_removed():
    assert _director_key("Jean-Luc Godard") == _director_key("Jean Luc Godard")


# ── _normalize_title ──────────────────────────────────────────────────────────


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
