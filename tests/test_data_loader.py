import pandas as pd

from utils.data_loader import build_taste_profile, build_watchlist_showtimes, future_showtimes

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


# ---------------------------------------------------------------------------
# build_watchlist_showtimes — dedup branch
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
