"""Tests for ui.ics — screening-block sizing and the RFC 5545 ICS writer."""

from __future__ import annotations

import re

import pandas as pd
import pytest

from ui import ADS_MINUTES_CHAIN, ADS_MINUTES_DEFAULT, screening_end, to_ics
from ui.ics import _ads_minutes, _ics_escape

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


# ── screening_end / _ads_minutes ────────────────────────────────────────────


@pytest.mark.parametrize(
    "theater_name",
    ["MK2 Bibliothèque", "mk2 Odéon", "UGC Ciné Cité Les Halles", "ugc les halles", "Ugc Normandie"],
)
def test_ads_minutes_chain_theaters_case_insensitive(theater_name):
    assert _ads_minutes(theater_name) == ADS_MINUTES_CHAIN == 20


@pytest.mark.parametrize("theater_name", ["Le Champo", "Christine Cinéma Club", "Cinémathèque Française"])
def test_ads_minutes_other_theaters(theater_name):
    assert _ads_minutes(theater_name) == ADS_MINUTES_DEFAULT == 10


@pytest.mark.parametrize("theater_name", [None, "", float("nan")])
def test_ads_minutes_missing_theater_falls_back_to_default(theater_name):
    assert _ads_minutes(theater_name) == ADS_MINUTES_DEFAULT


def test_screening_end_adds_chain_ads_to_runtime():
    row = pd.Series({"runtime_minutes": 112, "theater_name": "MK2 Beaubourg"})

    assert screening_end(row, pd.Timestamp("2026-08-03 19:30")) == pd.Timestamp("2026-08-03 21:42")  # 20 ads + 112


def test_screening_end_adds_default_ads_to_runtime():
    row = pd.Series({"runtime_minutes": 112, "theater_name": "Le Champo"})

    assert screening_end(row, pd.Timestamp("2026-08-03 19:30")) == pd.Timestamp("2026-08-03 21:32")  # 10 ads + 112


@pytest.mark.parametrize("runtime", [None, float("nan"), "", "not-a-number"])
def test_screening_end_unusable_runtime_falls_back_to_120_plus_ads(runtime):
    row = pd.Series({"runtime_minutes": runtime, "theater_name": "UGC Danton"})

    assert screening_end(row, pd.Timestamp("2026-08-03 19:30")) == pd.Timestamp("2026-08-03 21:50")  # 20 ads + 120


def test_screening_end_missing_theater_column():
    row = pd.Series({"runtime_minutes": 90})

    assert screening_end(row, pd.Timestamp("2026-08-03 19:30")) == pd.Timestamp("2026-08-03 21:10")  # 10 ads + 90
