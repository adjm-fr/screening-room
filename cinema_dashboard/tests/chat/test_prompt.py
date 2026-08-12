"""Tests for chat.prompt — the streaming context block and the title->slug map."""

from __future__ import annotations

import pandas as pd

from chat.prompt import STREAMING_CONTEXT_TOP_N, _slug_by_title, _streaming_context


def test_streaming_context_empty_when_no_columns():
    df = pd.DataFrame({"letterboxd_title": ["A"]})
    assert _streaming_context(df) == ""


def test_streaming_context_skips_films_without_providers():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["No Streaming", "Has Streaming"],
            "flatrate": [[], ["mubi"]],
        }
    )
    out = _streaming_context(df)
    assert "Has Streaming" in out
    assert "mubi" in out
    assert "No Streaming" not in out


def test_streaming_context_dedups_by_title():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Same", "Same"],
            "flatrate": [["mubi"], ["mubi"]],
        }
    )
    assert _streaming_context(df).count("Same") == 1


def test_streaming_context_appends_free_segment_when_present():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Has Free"],
            "flatrate": [["mubi"]],
            "free": [["arte"]],
        }
    )
    out = _streaming_context(df)
    assert "flatrate=mubi" in out
    assert "; free=arte" in out


def test_streaming_context_omits_free_segment_when_absent():
    df = pd.DataFrame(
        {
            "letterboxd_title": ["No Free"],
            "flatrate": [["mubi"]],
            "free": [[]],
        }
    )
    out = _streaming_context(df)
    assert "flatrate=mubi" in out
    assert "free=" not in out


def test_streaming_context_includes_free_only_film():
    # A film with no flatrate provider but a free one must still surface —
    # free platforms are available to everyone.
    df = pd.DataFrame(
        {
            "letterboxd_title": ["Free Only"],
            "flatrate": [[]],
            "free": [["arte"]],
        }
    )
    out = _streaming_context(df)
    assert "Free Only" in out
    assert "; free=arte" in out


def _streaming_frame(n: int, *, scored: bool) -> pd.DataFrame:
    """``n`` distinct streaming films, descending match when ``scored``."""
    rows = [
        {
            "letterboxd_title": f"Film {i:03d}",
            "flatrate": ["mubi"],
            "free": [],
            **({"match": float(n - i)} if scored else {}),
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


def test_streaming_context_falls_back_to_full_list_without_a_match_column():
    # No scores available (build_chat_context's fallback path) — narrowing
    # would need a ranking it doesn't have, so the block stays uncapped.
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=False)
    out = _streaming_context(df)
    assert out.count("Film ") == STREAMING_CONTEXT_TOP_N + 25
    assert "streaming_query" not in out  # nothing was left out, so no marker


def test_streaming_context_caps_to_top_n_when_scored():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)
    out = _streaming_context(df)
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    assert len(lines) == STREAMING_CONTEXT_TOP_N


def test_streaming_context_keeps_the_highest_match_films_when_capped():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)  # match descends as i increases
    out = _streaming_context(df)
    assert "Film 000" in out  # highest match (n - 0)
    assert f"Film {STREAMING_CONTEXT_TOP_N + 24:03d}" not in out  # lowest match, narrowed out


def test_streaming_context_appends_marker_line_when_truncated():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 25, scored=True)
    out = _streaming_context(df)
    assert "streaming_query" in out
    assert "+25 more" in out


def test_streaming_context_no_marker_line_when_under_the_cap():
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N - 5, scored=True)
    out = _streaming_context(df)
    assert "streaming_query" not in out
    assert out.count("- Film ") == STREAMING_CONTEXT_TOP_N - 5


def test_streaming_context_marker_line_is_not_in_the_pinned_line_format():
    # The eval goldens pin "- {title} — flatrate=...": the marker must never
    # be mistaken for a film entry by a model or a test parsing that shape.
    df = _streaming_frame(STREAMING_CONTEXT_TOP_N + 1, scored=True)
    out = _streaming_context(df)
    marker = next(line for line in out.splitlines() if "streaming_query" in line)
    assert not marker.startswith("- ")


def test_slug_by_title_keys_both_title_spellings():
    """A pin's stored title may be either spelling, depending on what the join matched."""
    wl = pd.DataFrame([{"title": "Fail Safe", "french_title": "Point limite", "slug": "fail-safe", "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {
        "Fail Safe": [("fail-safe", "Sidney Lumet")],
        "Point limite": [("fail-safe", "Sidney Lumet")],
    }


def test_slug_by_title_keeps_every_film_sharing_a_title():
    """Remakes collide; last-write-wins would silently point a pin at the wrong film."""
    wl = pd.DataFrame(
        [
            {"title": "King Lear", "french_title": "Le Roi Lear", "slug": "king-lear", "directors": "Peter Brook"},
            {"title": "King Lear", "french_title": "Le Roi Lear", "slug": "king-lear-1987", "directors": "Jean-Luc Godard"},
        ]
    )

    assert _slug_by_title(wl)["King Lear"] == [("king-lear", "Peter Brook"), ("king-lear-1987", "Jean-Luc Godard")]


def test_slug_by_title_does_not_duplicate_a_film_across_title_spellings():
    """title == french_title must not list the same slug twice."""
    wl = pd.DataFrame([{"title": "Güeros", "french_title": "Güeros", "slug": "gueros", "directors": "Alonso Ruizpalacios"}])

    assert _slug_by_title(wl)["Güeros"] == [("gueros", "Alonso Ruizpalacios")]


def test_slug_by_title_skips_rows_without_a_slug():
    wl = pd.DataFrame([{"title": "Fail Safe", "french_title": "Point limite", "slug": None, "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {}


def test_slug_by_title_without_a_slug_column_is_empty():
    assert _slug_by_title(pd.DataFrame([{"title": "Fail Safe"}])) == {}


def test_slug_by_title_tolerates_a_missing_french_title_column():
    wl = pd.DataFrame([{"title": "Fail Safe", "slug": "fail-safe", "directors": "Sidney Lumet"}])

    assert _slug_by_title(wl) == {"Fail Safe": [("fail-safe", "Sidney Lumet")]}


def test_slug_by_title_tolerates_a_missing_directors_column():
    wl = pd.DataFrame([{"title": "Fail Safe", "slug": "fail-safe"}])

    assert _slug_by_title(wl) == {"Fail Safe": [("fail-safe", "")]}
