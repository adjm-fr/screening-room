"""Tests for utils.ui — pure helpers (no Streamlit context required)."""

from __future__ import annotations

import re

import pytest

from utils.ui import _ics_escape, format_runtime, rating_to_hsl, to_ics

# ── format_runtime ──────────────────────────────────────────────────────────


def test_format_runtime_zero_returns_em_dash():
    assert format_runtime(0) == "—"


def test_format_runtime_none_returns_em_dash():
    assert format_runtime(None) == "—"


def test_format_runtime_invalid_string_returns_em_dash():
    assert format_runtime("not a number") == "—"  # type: ignore[arg-type]


def test_format_runtime_one_hour():
    assert format_runtime(60) == "1h00"


def test_format_runtime_two_hours_twelve():
    assert format_runtime(132) == "2h12"


def test_format_runtime_float_input():
    assert format_runtime(132.7) == "2h12"


def test_format_runtime_under_one_hour():
    assert format_runtime(45) == "0h45"


def test_format_runtime_preformatted_string():
    """Already-formatted strings should pass through."""
    assert format_runtime("1h 25min") == "1h 25min"


def test_format_runtime_preformatted_string_with_hour_suffix():
    """String with 'h' should be recognized as pre-formatted."""
    assert format_runtime("2h30") == "2h30"


# ── rating_to_hsl ───────────────────────────────────────────────────────────


def test_rating_to_hsl_none_is_transparent():
    assert rating_to_hsl(None) == "transparent"


def test_rating_to_hsl_high_score_dark():
    # rating 10 → lightness 80 - 10*4 = 40
    assert rating_to_hsl(10) == "hsl(36 80% 40%)"


def test_rating_to_hsl_low_score_light():
    assert rating_to_hsl(0) == "hsl(36 80% 80%)"


def test_rating_to_hsl_clamps_above_ten():
    assert rating_to_hsl(15) == "hsl(36 80% 40%)"


def test_rating_to_hsl_clamps_below_zero():
    assert rating_to_hsl(-3) == "hsl(36 80% 80%)"


def test_rating_to_hsl_invalid_string_is_transparent():
    assert rating_to_hsl("not a number") == "transparent"  # type: ignore[arg-type]


# ── _ics_escape ─────────────────────────────────────────────────────────────


def test_ics_escape_comma():
    assert _ics_escape("a,b") == "a\\,b"


def test_ics_escape_semicolon():
    assert _ics_escape("a;b") == "a\\;b"


def test_ics_escape_newline():
    assert _ics_escape("line1\nline2") == "line1\\nline2"


def test_ics_escape_backslash_first():
    assert _ics_escape("a\\b") == "a\\\\b"


# ── to_ics ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_events():
    return [
        {
            "summary": "Parasite",
            "start": "2026-05-10T19:30:00",
            "end": "2026-05-10T21:42:00",
            "location": "Le Champo",
            "description": "Bong Joon-ho",
            "uid": "fixed-uid-1",
        },
        {
            "summary": "Drive, My Car",
            "start": "2026-05-11T20:00:00",
            "end": "2026-05-11T22:59:00",
            "uid": "fixed-uid-2",
        },
    ]


def test_to_ics_starts_and_ends_correctly(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert out.startswith("BEGIN:VCALENDAR\r\n")
    assert out.rstrip("\r\n").endswith("END:VCALENDAR")


def test_to_ics_one_vevent_per_input(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert out.count("BEGIN:VEVENT") == 2
    assert out.count("END:VEVENT") == 2


def test_to_ics_dtstart_floating_local_format(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    # RFC 5545 floating-local form: YYYYMMDDTHHMMSS (no Z, no TZID)
    assert re.search(r"DTSTART:20260510T193000", out)


def test_to_ics_summary_with_comma_is_escaped(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert "SUMMARY:Drive\\, My Car" in out


def test_to_ics_uses_provided_uid(sample_events):
    out = to_ics(sample_events).decode("utf-8")
    assert "UID:fixed-uid-1" in out
    assert "UID:fixed-uid-2" in out


def test_to_ics_generates_uid_when_missing():
    events = [{"summary": "X", "start": "2026-01-01T10:00:00", "end": "2026-01-01T12:00:00"}]
    out = to_ics(events).decode("utf-8")
    assert re.search(r"UID:[0-9a-f-]+@cinema_dashboard", out)


def test_to_ics_crlf_line_endings(sample_events):
    out = to_ics(sample_events)
    assert b"\r\n" in out
    # No bare LF without preceding CR — RFC 5545 §3.1
    assert not re.search(rb"(?<!\r)\n", out)


def test_to_ics_round_trips_utf8():
    events = [
        {
            "summary": "Amélie",
            "start": "2026-01-01T10:00:00",
            "end": "2026-01-01T12:00:00",
            "location": "Cinéma Saint-Germain",
        }
    ]
    out = to_ics(events).decode("utf-8")
    assert "Amélie" in out
    assert "Cinéma Saint-Germain" in out


def test_to_ics_omits_optional_fields_when_missing():
    events = [{"summary": "X", "start": "2026-01-01T10:00:00", "end": "2026-01-01T12:00:00"}]
    out = to_ics(events).decode("utf-8")
    assert "LOCATION" not in out
    assert "DESCRIPTION" not in out
