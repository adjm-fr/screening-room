"""
Calendar export: screening-block sizing, the RFC 5545 writer, and both builders.

The single source of calendar-block duration (:func:`screening_end`) is
shared by the calendar page's ICS *and* CSV exports and by the movie detail
page's per-screening ``.ics`` — keeping them on one helper is what stops the
three from drifting.

Both of the calendar page's export builders live here too
(:func:`build_ics_events`, :func:`build_csv_rows`) rather than in the page, for
the same anti-drift reason: they are the two other ``screening_end`` callers,
and a builder that lives in a page module is a builder a page can quietly fork.
Each takes the *whole* filtered frame the agenda is rendered from, so the
download always matches what is on screen — see ``core.agenda`` for the one
filter chain that produces it.
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


def _summary_of(row: pd.Series) -> str:
    return str(row.get("letterboxd_title") or row.get("french_title") or "Screening")


def _location_of(row: pd.Series) -> str:
    return str(row.get("theater_name") or row.get("theater_id", ""))


def _description_of(row: pd.Series) -> str:
    return f"Directors: {row.get('directors') or 'N/A'} | French title: {row.get('french_title')}"


def build_ics_events(df: pd.DataFrame) -> list[dict]:
    """Build :func:`to_ics` event dicts from a frame of screenings.

    One event per row (i.e. per screening, not per film), sized with
    :func:`screening_end`. Rows whose showtime is missing or unparseable are
    skipped rather than exported as a zero-length block.
    """
    events: list[dict] = []
    for idx, row in df.iterrows():
        showtime = pd.to_datetime(row["showtimes"], errors="coerce")
        if pd.isna(showtime):
            continue
        events.append(
            {
                "summary": _summary_of(row),
                "start": showtime,
                "end": screening_end(row, showtime),
                "location": _location_of(row),
                "description": _description_of(row),
                "uid": f"{idx}-{int(showtime.timestamp())}@cinema_dashboard",
            }
        )
    return events


#: Leading characters that make a spreadsheet treat a cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralise a spreadsheet formula prefix on an exported text cell.

    Film titles, directors and theater names are scraped from Allocine/Letterboxd/
    TMDB, so a title starting with ``=``/``+``/``-``/``@`` would be evaluated as a
    formula when the export is opened in Excel or Sheets rather than shown as text.
    Prefixing with an apostrophe is the standard fix — the spreadsheet consumes it
    as a "treat the rest as text" marker instead of displaying it. Only touches
    cells that actually start with one of those characters, so ordinary titles are
    exported byte-for-byte.

    Only the CSV path needs this; ICS is not formula-interpreted (see
    :func:`_ics_escape`).
    """
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def build_csv_rows(df: pd.DataFrame) -> list[dict]:
    """Build Google-Calendar CSV-import rows from a frame of screenings.

    The three free-text columns pass through :func:`_csv_safe`; the date/time
    columns are generated here and need no guard.

    The legacy import path, kept behind an expander in the export popover. Uses
    the same :func:`screening_end` sizing and the same skip rule as
    :func:`build_ics_events`, so the two downloads can never disagree about when
    a screening ends. The nine keys are Google's import header, in its order.
    """
    rows: list[dict] = []
    for _, row in df.iterrows():
        showtime = pd.to_datetime(row["showtimes"], errors="coerce")
        if pd.isna(showtime):
            continue
        end_time = screening_end(row, showtime)
        rows.append(
            {
                "Subject": _csv_safe(_summary_of(row)),
                "Start Date": showtime.strftime("%Y-%m-%d"),
                "Start Time": showtime.strftime("%H:%M:%S"),
                "End Date": end_time.strftime("%Y-%m-%d"),
                "End Time": end_time.strftime("%H:%M:%S"),
                "All Day Event": "False",
                "Description": _csv_safe(_description_of(row)),
                "Location": _csv_safe(_location_of(row)),
                "Private": "False",
            }
        )
    return rows


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
