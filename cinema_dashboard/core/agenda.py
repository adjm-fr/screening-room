"""
Agenda model for the Watchlist Showtimes page: filtering, grouping, labelling.

The page renders a compact vertical agenda — one row per (film × day), that
day's showtimes listed inside the row — instead of one horizontal poster rail
per day. Everything that decides *which* screenings appear and *how they group*
lives here, Streamlit-free, so it is unit-testable without an app context;
``ui.agenda`` only turns the result into HTML.

Two structural rules this module exists to enforce:

- **One filter chain, one frame.** :func:`apply_filters` applies every control
  except the day strip and :func:`apply_day` folds that in; the single frame
  they produce is what the agenda, the ICS export, the CSV export and the map
  all read. A new filter is added by extending :class:`AgendaFilters` and
  :func:`apply_filters` — never by narrowing again downstream, which is how the
  export and the screen would silently diverge.
- **Films are grouped by slug, not by title.** 22 titles in the real watchlist
  name two different films (*King Lear* is Brook's *and* Godard's, *Mandy* is
  Mackendrick's *and* Cosmatos'), so a title-keyed group merges two films into
  one row carrying both films' showtimes. ``_film_key`` prefers
  ``letterboxd_slug`` and only falls back to a title when there is none — the
  same reasoning as ``chat.ui.resolve_pin``'s title→list mapping.

``_film_key`` resolves *identity*; ``ui.cards._title_of`` resolves *display*
(canonical Letterboxd title first). They look similar and are not
interchangeable — do not unify them.

Public API:
    runtime_bucket(minutes) -> str
    time_bucket(when) -> str
    day_label(day, *, today) -> str
    day_chip_label(day) -> str
    with_agenda_columns(df) -> pd.DataFrame
    AgendaFilters / apply_filters(df, filters) / apply_day(df, day)
    DayChip / day_chips(df)
    AgendaShowtime / AgendaEntry / AgendaDay / build_agenda(df, *, sort, today)
    agenda_kpis(df) -> list[tuple[str, int]]
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, datetime, time, timedelta
from typing import Literal

import pandas as pd

from core.availability import free_time_mask

#: Time-of-day buckets in display order: (label, start_hour, end_hour_exclusive).
#: Four coarse buckets replace the old 15-minute time-range slider — a decision
#: with roughly four real answers does not need 96 stops.
TIME_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("Morning", 0, 12),
    ("Afternoon", 12, 18),
    ("Evening", 18, 22),
    ("Late", 22, 24),
)
#: Just the labels, in display order — the chip-filter options.
TIME_BUCKET_LABELS: tuple[str, ...] = tuple(label for label, _, _ in TIME_BUCKETS)

#: Runtime buckets, in display order. ``"Unknown"`` is a possible *result* of
#: :func:`runtime_bucket` but deliberately not an offered filter option.
RUNTIME_BUCKETS: tuple[str, ...] = ("<90", "90–120", ">120")

#: Columns :func:`with_agenda_columns` derives. Private to the agenda layer.
_DERIVED_COLUMNS = ("_dt", "_day", "_time_bucket", "_runtime_bucket", "_film_key")

#: Title/identity columns searched by :attr:`AgendaFilters.search`, in the order
#: ``_film_key`` prefers them.
_KEY_COLUMNS = ("letterboxd_slug", "letterboxd_title", "french_title")

_HOURS_RE = re.compile(r"(\d+)\s*h", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*(?:min|m)", re.IGNORECASE)
#: "2h30" / "2h12" — bare minutes trailing the hour marker with no "m" suffix,
#: which is exactly what :func:`ui.theme.format_runtime` emits.
_BARE_TAIL_RE = re.compile(r"\d+\s*h\s*(\d+)", re.IGNORECASE)


def _parse_runtime(text: str) -> float | None:
    """Minutes from a runtime string (``"1h 52min"``, ``"2h12"``, ``"95 min"``, ``"112"``).

    Returns ``None`` when nothing numeric can be recovered. Stripping non-digits
    and concatenating them — the previous approach — turned ``"1h 52min"`` into
    152 minutes instead of 112, silently and without raising.
    """
    hours = _HOURS_RE.search(text)
    minutes = _MINUTES_RE.search(text)
    if hours or minutes:
        total = (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)
        if hours and not minutes:
            tail = _BARE_TAIL_RE.search(text)
            if tail:
                total += int(tail.group(1))
        return float(total)
    try:
        return float(text)
    except ValueError:
        return None


def runtime_bucket(minutes: float | int | str | None) -> str:
    """Bucket a runtime into ``"<90"`` / ``"90–120"`` / ``">120"`` / ``"Unknown"``.

    Accepts the numeric ``runtime_minutes`` the watchlist carries *and* the raw
    ``"1h 52min"`` strings the SHOWTIMES contract carries, so the bucket stays
    correct whichever side of the join supplies the column.
    """
    if minutes is None:
        return "Unknown"
    if isinstance(minutes, str):
        text = minutes.strip()
        parsed = _parse_runtime(text) if text else None
    else:
        try:
            parsed = None if pd.isna(minutes) else float(minutes)
        except (TypeError, ValueError):
            parsed = None
    if parsed is None:
        return "Unknown"
    total = int(parsed)
    if total < 90:
        return "<90"
    if total <= 120:
        return "90–120"
    return ">120"


def time_bucket(when: pd.Timestamp) -> str:
    """Bucket a showtime into one of :data:`TIME_BUCKET_LABELS` (``"Unknown"`` for NaT)."""
    if when is None or pd.isna(when):
        return "Unknown"
    hour = pd.Timestamp(when).hour
    return next((label for label, start, end in TIME_BUCKETS if start <= hour < end), "Unknown")


def day_label(day: date, *, today: date | None = None) -> str:
    """Friendly heading for a day: ``"Tonight"`` / ``"Tomorrow"`` / ``"Friday 07 August"``.

    ``today`` is injected rather than read from the clock so the labels are
    testable — without it every assertion here would go red at midnight.
    """
    today = today or date.today()
    if day == today:
        return "Tonight"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    return day.strftime("%A %d %B")


def day_chip_label(day: date) -> str:
    """Short label for a day chip: ``"Tue 4"``.

    Built by interpolating ``day.day`` rather than with ``%-d``, which is a
    glibc/BSD ``strftime`` extension and not portable.
    """
    return f"{day:%a} {day.day}"


def _as_date(value: object) -> date | None:
    """Coerce a ``_day`` groupby key into a ``date``, or ``None`` if it isn't one.

    ``groupby`` keys are typed ``Hashable`` and, because every ``_day`` grouping
    here passes ``dropna=False``, a null ``_day`` surfaces as a ``NaT``/``NaN``
    key rather than being dropped. That key would reach ``day_chip_label``'s
    ``%a`` formatting and raise, so both callers skip whatever this rejects.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        timestamp = pd.Timestamp(str(value))
    except ValueError:
        return None
    # ``pd.NaT`` is not a ``Timestamp`` instance, so this rejects null keys too.
    return timestamp.date() if isinstance(timestamp, pd.Timestamp) else None


def _film_key_series(df: pd.DataFrame) -> pd.Series:
    """Identity key per row: slug, else Letterboxd title, else Allocine title, else ``""``.

    Always a string, never NaN: ``DataFrame.groupby`` defaults to
    ``dropna=True``, so a NaN key would make the row vanish from the agenda with
    no error at all.
    """
    key = pd.Series("", index=df.index, dtype="object")
    for col in _KEY_COLUMNS:
        if col in df.columns:
            candidate = df[col].fillna("").astype(str).str.strip()
            key = key.where(key != "", candidate)
    return key


def with_agenda_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the derived agenda columns and drop screenings with no usable time.

    Adds ``_dt`` (coerced showtime), ``_day``, ``_time_bucket``,
    ``_runtime_bucket`` and ``_film_key``. Rows whose showtime is missing or
    unparseable are dropped — a screening we cannot place in time cannot be on
    an agenda, mirroring :func:`core.availability.free_time_mask`'s NaT rule.

    Idempotent (returns the frame unchanged once ``_dt`` is present) so callers
    can apply it defensively, and never mutates its argument.
    """
    if "_dt" in df.columns:
        return df

    out = df.copy()
    if "showtimes" in out.columns:
        out["_dt"] = pd.to_datetime(out["showtimes"], errors="coerce")
    else:
        out["_dt"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    out = out[out["_dt"].notna()].copy()

    out["_day"] = out["_dt"].dt.date
    out["_time_bucket"] = out["_dt"].apply(time_bucket)
    runtime_col = next((c for c in ("runtime_minutes", "runtime") if c in out.columns), None)
    out["_runtime_bucket"] = out[runtime_col].apply(runtime_bucket) if runtime_col else "Unknown"
    out["_film_key"] = _film_key_series(out)
    return out


@dataclasses.dataclass(frozen=True)
class AgendaFilters:
    """Every calendar-page control except the day strip, as one value object.

    Each field's default is its "off" position, so ``apply_filters(df,
    AgendaFilters())`` is the identity (modulo :func:`with_agenda_columns`).
    Empty collections mean "all", matching the page's existing convention that
    an empty theater multiselect shows every theater.
    """

    search: str = ""
    theaters: tuple[str, ...] = ()
    runtimes: tuple[str, ...] = ()
    time_buckets: tuple[str, ...] = ()
    min_rating: float = 0.0
    only_free: bool = False
    free_cutoff: time = time(19, 0)
    days_off: tuple[date, ...] = ()
    unavailable: tuple[date, ...] = ()

    def active_count(self) -> int:
        """How many controls are away from their default — the popover's badge count."""
        return sum(
            [
                bool(self.search.strip()),
                bool(self.theaters),
                bool(self.runtimes),
                bool(self.time_buckets),
                self.min_rating > 0,
                self.only_free,
            ]
        )


def apply_filters(df: pd.DataFrame, filters: AgendaFilters) -> pd.DataFrame:
    """Narrow the screenings frame by every control except the day strip.

    Cheap, selective ``isin`` filters run first so the row-wise
    :func:`core.availability.free_time_mask` (which builds a public-holiday set)
    runs last, over the smallest frame. Every step guards on column presence, so
    a frame missing an optional column narrows on what it has instead of
    raising.
    """
    out = with_agenda_columns(df)
    if out.empty:
        return out

    if filters.theaters and "theater_name" in out.columns:
        out = out[out["theater_name"].isin(filters.theaters)]
    if filters.runtimes:
        out = out[out["_runtime_bucket"].isin(filters.runtimes)]
    if filters.time_buckets:
        out = out[out["_time_bucket"].isin(filters.time_buckets)]
    if filters.min_rating > 0 and "letterboxd_avg_rating" in out.columns:
        out = out[out["letterboxd_avg_rating"].fillna(0) >= filters.min_rating]

    needle = filters.search.strip().casefold()
    if needle and not out.empty:
        # Both title spellings, because `french_title` here is Allocine's display
        # title (a repertory screening often runs under the original title) —
        # searching only it makes a film unreachable by its Letterboxd name.
        mask = pd.Series(False, index=out.index)
        for col in ("letterboxd_title", "french_title", "directors"):
            if col in out.columns:
                mask = mask | out[col].fillna("").astype(str).str.casefold().str.contains(needle, regex=False)
        out = out[mask]

    if filters.only_free and not out.empty:
        out = out[
            free_time_mask(
                out["_dt"],
                cutoff=filters.free_cutoff,
                days_off=filters.days_off,
                unavailable=filters.unavailable,
            )
        ]
    return out


def apply_day(df: pd.DataFrame, day: date | None) -> pd.DataFrame:
    """Scope the frame to one day; ``None`` (the "All" chip) is the identity.

    Applied *after* :func:`apply_filters` so the day chips can be built from the
    fully-filtered frame, and so the selection scopes the export as well as the
    screen.
    """
    if day is None or "_day" not in df.columns:
        return df
    return df[df["_day"] == day]


@dataclasses.dataclass(frozen=True)
class DayChip:
    """One option in the day strip. ``day is None`` is the leading "All" chip."""

    day: date | None
    label: str
    count: int


def day_chips(df: pd.DataFrame) -> list[DayChip]:
    """Build the day strip's options: an "All" chip, then one per day with counts.

    Counts are **screenings**, not films, consistently across every chip. Returns
    ``[]`` for an empty frame — a strip with only an "All · 0" chip is worse
    than no strip.
    """
    if df.empty or "_day" not in df.columns:
        return []
    counts = df.groupby("_day", sort=True, dropna=False).size()
    chips = [DayChip(day=None, label="All", count=int(len(df)))]
    for key, n in zip(counts.index, counts.to_numpy(), strict=True):
        day = _as_date(key)
        if day is not None:
            chips.append(DayChip(day=day, label=day_chip_label(day), count=int(n)))
    return chips


@dataclasses.dataclass(frozen=True)
class AgendaShowtime:
    """One screening inside an agenda row: when, and where."""

    when: pd.Timestamp
    theater: str


# ``eq=False`` on the two dataclasses holding a ``pd.Series``: a generated
# ``__eq__`` would compare Series element-wise and raise "truth value of a Series
# is ambiguous" the first time anything compares two entries.
@dataclasses.dataclass(frozen=True, eq=False)
class AgendaEntry:
    """One film on one day, with every showtime it has that day."""

    row: pd.Series
    showtimes: tuple[AgendaShowtime, ...]
    earliest: pd.Timestamp
    match: float | None


@dataclasses.dataclass(frozen=True, eq=False)
class AgendaDay:
    """One day section of the agenda."""

    day: date
    label: str
    is_today: bool
    entries: tuple[AgendaEntry, ...]

    @property
    def film_count(self) -> int:
        return len(self.entries)


def _entry_match(row: pd.Series) -> float | None:
    value = row.get("match")
    if not isinstance(value, (int, float)) or pd.isna(value):
        return None
    return float(value)


def build_agenda(
    df: pd.DataFrame,
    *,
    sort: Literal["time", "match"] = "time",
    today: date | None = None,
) -> list[AgendaDay]:
    """Group screenings into day sections, one entry per (film × day).

    Days are always chronological. ``sort`` reorders entries *within* each day —
    never across days, because the day strip is itself a day picker and a flat
    list would leave that control pointing at nothing. Showtimes inside an entry
    are chronological under both modes: "19:00 then 21:30" is a fact about the
    evening, not a ranking.

    ``sort="match"`` puts unscored entries last rather than first, so a missing
    taste profile degrades to "unranked at the bottom" instead of "ranked best".
    """
    out = with_agenda_columns(df)
    if out.empty:
        return []
    today = today or date.today()

    days: list[AgendaDay] = []
    for day, day_group in out.groupby("_day", sort=True, dropna=False):
        entries: list[AgendaEntry] = []
        for _, film_group in day_group.groupby("_film_key", sort=False, dropna=False):
            ordered = film_group.sort_values("_dt")
            rep = ordered.iloc[0]
            showtimes = tuple(
                AgendaShowtime(when=row["_dt"], theater=str(row.get("theater_name") or "")) for _, row in ordered.iterrows()
            )
            entries.append(
                AgendaEntry(
                    row=rep,
                    showtimes=showtimes,
                    earliest=showtimes[0].when,
                    match=_entry_match(rep),
                )
            )

        if sort == "match":
            entries.sort(key=lambda e: (e.match is None, -(e.match or 0.0), e.earliest, str(e.row.get("_film_key", ""))))
        else:
            entries.sort(key=lambda e: (e.earliest, str(e.row.get("_film_key", ""))))

        day_value = _as_date(day)
        if day_value is None:
            continue
        days.append(
            AgendaDay(
                day=day_value,
                label=day_label(day_value, today=today),
                is_today=day_value == today,
                entries=tuple(entries),
            )
        )
    return days


def agenda_kpis(df: pd.DataFrame) -> list[tuple[str, int]]:
    """The four headline counts, shaped for :func:`ui.chips.render_kpi_strip`."""
    out = with_agenda_columns(df)
    if out.empty:
        return [("Films", 0), ("Screenings", 0), ("Theaters", 0), ("Nights", 0)]
    theaters = int(out["theater_name"].nunique()) if "theater_name" in out.columns else 0
    return [
        ("Films", int(out["_film_key"].nunique())),
        ("Screenings", int(len(out))),
        ("Theaters", theaters),
        ("Nights", int(out["_day"].nunique())),
    ]
