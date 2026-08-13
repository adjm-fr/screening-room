"""
The Screening in Paris lenses: which of this week's films are worth your time.

Three questions asked of every screening — "is this new to me?", "did I dislike
it while the ranker says I would like it now?", "would I happily see it again?" —
as a category per row (:func:`categorize`), the counts behind the chip strip
(:func:`lens_counts`), and the cut that removes seen films answering none of them
(:func:`drop_uninteresting_seen`).

**Deliberately its own module rather than part of :mod:`core.agenda`.** The lens
vocabulary is Paris-only: ``watch_status`` and ``user_rating`` come from
:func:`sources.discover.build_screenings` and do not exist on the calendar page's
``wl_shows`` frame, so folding these into :class:`~core.agenda.AgendaFilters` would
push page vocabulary into the shared filter chain. A separate ``core`` module
carries no such coupling — it is Streamlit-free pure pandas, which is what ``core``
is for — and the lens stays a scoping step applied *after* ``apply_filters``,
exactly as ``apply_day`` is.

It lives here rather than in ``pages/paris.py`` for the reason
``pages/calendar.py`` is only 329 lines: on this codebase's boundary a page renders
and a ``core`` module decides, so pure pandas in a page module is misfiled. Every
function here is total — a missing column means that category simply never fires,
never an exception.
"""

from __future__ import annotations

import pandas as pd

#: "Worth a rewatch" — films you rated at least this. On the ratings ladder
#: (see CLAUDE.md) 3.5–4 is "must watch" and 4.5–5 "masterpiece", so 4.0 is
#: the floor of "I'd happily sit through it again".
REWATCH_MIN_RATING = 4.0

#: "Worth a second chance" — films you rated *below* this. 2.5 is the ladder's
#: bottom of "good", so under it is the genuinely-didn't-land band.
RETRY_MAX_RATING = 2.5

#: …but only where the ranker disagrees with that verdict this strongly. The
#: match is a 0–100 logistic (``core.taste.match_from_raw``) and 70 sits near
#: the top of the observed distribution, so the lens stays short and pointed
#: instead of re-listing everything you were lukewarm on.
RETRY_MIN_MATCH = 70.0

#: Sentinel option value for the "All" lens chip, mirroring
#: :data:`ui.agenda.DAY_ALL` — a ``None`` option would be indistinguishable
#: from "nothing selected" in the widget's return value.
LENS_ALL = "all"

#: Lens value → chip label, in display order. Keys are the exact strings
#: :func:`categorize` emits; ``ui.agenda`` keys its row badges on the same
#: values.
LENS_LABELS: dict[str, str] = {
    "new": "✨ New to you",
    "second_chance": "🔄 Worth a second chance",
    "rewatch": "⭐ Worth a rewatch",
}


def categorize(df: pd.DataFrame) -> pd.Series:
    """Assign each screening row at most one lens category, aligned to ``df.index``.

    Returns an object-dtype Series of ``"new"`` (``watch_status ==
    "untracked"``), ``"second_chance"`` (``user_rating < RETRY_MAX_RATING``
    **and** ``match >= RETRY_MIN_MATCH`` — the disagreement lens), ``"rewatch"``
    (``user_rating >= REWATCH_MIN_RATING``), else ``None``. Object dtype with
    ``None`` rather than a nullable string dtype on purpose: comparing an
    NA-backed dtype yields ``NA``s that pandas rejects as a boolean mask,
    whereas ``None == lens`` is plain ``False``.

    The categories are mutually exclusive by construction: an untracked film
    has no rating to cut on (``user_rating`` is mapped from the ratings
    parquet, and untracked means the slug isn't there), and the two rating
    cuts are disjoint bands.

    Total, never raises. A missing column simply means that category never
    fires; ``user_rating``/``match`` are coerced through
    ``pd.to_numeric(errors="coerce")`` and every mask is ``.fillna(False)``-ed,
    because both arrive nullable and ``series >= x`` on a nullable dtype
    yields ``NA``, which pandas refuses as a boolean mask.
    """
    # A list, not a scalar broadcast: pd.Series(None, ...) silently promotes
    # None to NaN even under dtype=object, and this function promises None.
    out = pd.Series([None] * len(df), index=df.index, dtype=object)
    if "watch_status" in df.columns:
        out[(df["watch_status"] == "untracked").fillna(False)] = "new"
    rating = pd.to_numeric(df["user_rating"], errors="coerce") if "user_rating" in df.columns else None
    match = pd.to_numeric(df["match"], errors="coerce") if "match" in df.columns else None
    if rating is not None and match is not None:
        out[((rating < RETRY_MAX_RATING) & (match >= RETRY_MIN_MATCH)).fillna(False)] = "second_chance"
    if rating is not None:
        out[(rating >= REWATCH_MIN_RATING).fillna(False)] = "rewatch"
    return out


def drop_uninteresting_seen(df: pd.DataFrame) -> pd.DataFrame:
    """Drop already-seen films that landed in neither "worth" lens.

    A ``"seen"`` film with no ``_category`` cleared neither bar: the ranker
    didn't flag it for a second chance (too well liked already, or too low a
    match) and it didn't clear the rewatch bar either. That is exactly the
    noise this page exists to cut — not just for one lens, but from the whole
    programme, so the KPI strip, the lens counts and the agenda all agree on
    what is left.

    Total: a frame missing either column is returned unchanged rather than
    raising, matching :func:`categorize`'s convention that an absent column
    means the category (here, the drop) never fires.
    """
    if "watch_status" not in df.columns or "_category" not in df.columns:
        return df
    drop = (df["watch_status"] == "seen") & df["_category"].isna()
    return df[~drop]


def lens_counts(df: pd.DataFrame) -> dict[str, int]:
    """Distinct-film count per lens, in :data:`LENS_LABELS` order, zeroes omitted.

    Films, not screenings — ``nunique`` on ``_film_key``, the slug-first
    identity :func:`core.agenda.with_agenda_columns` derives and the agenda
    groups on, so the chip counts, the KPI strip and the agenda all agree on
    what "one film" means. Omitting a zero-count lens is the old
    omit-empty-rail rule: "second chance" needs ``match``, so without a taste
    profile it simply never appears. Total: an empty frame, or one missing
    either column, returns ``{}``.
    """
    if df.empty or "_category" not in df.columns or "_film_key" not in df.columns:
        return {}
    counts = df.groupby("_category")["_film_key"].nunique()
    return {lens: int(counts[lens]) for lens in LENS_LABELS if lens in counts.index and int(counts[lens]) > 0}
