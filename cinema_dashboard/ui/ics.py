"""
ICS calendar export: screening-block sizing and the RFC 5545 writer.

The single source of calendar-block duration (:func:`screening_end`) is
shared by the calendar page's ICS *and* CSV exports and by the movie detail
page's per-screening ``.ics`` — keeping them on one helper is what stops the
three from drifting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd

#: Ads + trailers run longer in the big chains than in independent/arthouse theaters.
ADS_MINUTES_CHAIN = 20
ADS_MINUTES_DEFAULT = 10
#: Substrings (lowercase) identifying the chains that run the longer ad block.
_CHAIN_MARKERS = ("mk2", "ugc")


def _ads_minutes(theater_name: object) -> int:
    """Minutes of ads/trailers before the feature actually starts, by theater."""
    name = str(theater_name or "").lower()
    return ADS_MINUTES_CHAIN if any(marker in name for marker in _CHAIN_MARKERS) else ADS_MINUTES_DEFAULT


def screening_end(row: pd.Series, showtime: pd.Timestamp) -> pd.Timestamp:
    """End of the calendar block: showtime + pre-feature ads + runtime (120min fallback).

    The single source of calendar-block duration, shared by the calendar page's
    ICS *and* CSV exports and by the movie detail page's per-screening ``.ics``
    — keeping them on one helper is what stops the three from drifting.
    """
    runtime = row.get("runtime_minutes")
    try:
        runtime_min = int(float(runtime)) if runtime and not pd.isna(runtime) else 120
    except (ValueError, TypeError):
        runtime_min = 120
    return showtime + pd.Timedelta(minutes=_ads_minutes(row.get("theater_name")) + runtime_min)


def _ics_escape(value: str) -> str:
    """Escape a single ICS TEXT field per RFC 5545 §3.3.11."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n").replace("\r", "")


def to_ics(events: list[dict]) -> bytes:
    """Build an RFC 5545 ICS file from a list of event dicts.

    Each event dict requires ``summary``, ``start`` (datetime-like),
    ``end`` (datetime-like). Optional: ``location``, ``description``, ``uid``.
    Returns UTF-8 bytes with CRLF line endings (per RFC 5545 §3.1).

    Times are written in floating-local form (no Z suffix, no TZID) to
    minimise calendar-import surprises across Google/Apple/Outlook — the
    user's calendar shows them at their local clock time.
    """
    now_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//cinema_dashboard//watchlist//EN",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        start = pd.to_datetime(ev["start"]).strftime("%Y%m%dT%H%M%S")
        end = pd.to_datetime(ev["end"]).strftime("%Y%m%dT%H%M%S")
        uid = ev.get("uid") or f"{uuid4()}@cinema_dashboard"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{start}",
                f"DTEND:{end}",
                f"SUMMARY:{_ics_escape(str(ev['summary']))}",
            ]
        )
        if ev.get("location"):
            lines.append(f"LOCATION:{_ics_escape(str(ev['location']))}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_ics_escape(str(ev['description']))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
