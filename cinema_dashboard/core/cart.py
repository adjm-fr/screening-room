"""
The showtimes cart: pick individual screenings, keep them, export them as one ICS.

The Screening in Paris page's read surface answers "what is worth my time this
week"; this module is what turns an answer into a plan. The user ticks
individual showtimes across days, lenses and filters, and the accumulated set
exports as a single ``.ics``.

**This deliberately inverts the calendar page's export rule.** There, "the export
mirrors its on-screen filters" is structural: one frame feeds both the agenda and
the download, so picking a day scopes the ``.ics``. A cart is the opposite — an
explicit, hand-picked set that is *independent* of every filter, lens and day, so
a plan survives changing the view that produced it. Nothing here may narrow the
cart to the frame on screen; that would not be a fix, it would delete the feature.

Streamlit-free on purpose (``ui.cart`` is the surface), which is why
:func:`prune_past` takes ``now`` rather than reaching for a clock: ``core`` imports
nothing from ``sources``.

Three things here are load-bearing and each has a comment explaining why, because
each looks like an over-complication until it breaks:

- :func:`showtime_id` is a *hash*, and it is neither the DataFrame index label nor
  :func:`hash`.
- :func:`cart_frame` sets the frame's index to those ids, because
  ``ui.ics.build_ics_events`` derives its ICS ``UID`` from the index label.
- :func:`pick_group_key` keys a widget on its *option universe*, not on the film.

Public API:
    showtime_id(film_key, when, theater_id) -> str
    pick_group_key(ids) -> str
    CartItem / ScreeningCart
    snapshot(row) -> dict
    cart_index(df) -> dict[str, CartItem]
    entry_showtime_ids(entry) -> list[str]
    reconcile_group(cart, universe, selected, index) -> bool
    cart_frame(cart) -> pd.DataFrame
    prune_past(cart, now) -> int
    save_cart(cart, path) / load_cart(path) / delete_cart(path)
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import pandas as pd

from core.agenda import AgendaEntry, with_agenda_columns

log = logging.getLogger(__name__)

#: On-disk snapshot of the cart, in the gitignored ``data/`` dir beside
#: ``chat_state.json`` and the streaming/geo caches. Module-level so tests can
#: patch it (the ``chat.state.CHAT_STATE_PATH`` pattern); every helper below
#: resolves it at call time, not at function-definition time.
CART_PATH = Path("data") / "paris_cart.json"

#: Exactly what a cart item must carry to export without the original frame, and
#: nothing else. The first seven are read by the export path — ``showtimes`` and
#: ``theater_name`` by ``ui.ics.build_ics_events`` *and* ``ui.ics.screening_end``
#: (which sizes the calendar block from ``runtime_minutes`` plus the theater's ad
#: block), ``letterboxd_title``/``french_title`` by ``_summary_of``, ``theater_id``
#: as ``_location_of``'s fallback, ``directors`` by ``_description_of``.
#: ``letterboxd_slug`` is display only: the cart panel links each row to its
#: detail page. Pinned by
#: ``tests/core/test_cart.py::test_snapshot_fields_cover_everything_the_ics_builders_read``
#: — extend that test alongside this tuple.
CART_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "showtimes",
    "letterboxd_title",
    "french_title",
    "theater_name",
    "theater_id",
    "directors",
    "runtime_minutes",
    "letterboxd_slug",
)

#: Field separator inside a hashed payload. ASCII 31 (unit separator) rather than
#: a printable character so a film title containing the delimiter cannot forge
#: another screening's id.
_SEP = "\x1f"


def showtime_id(film_key: str, when: pd.Timestamp, theater_id: str) -> str:
    """Stable identity for one screening: 16 hex characters.

    **Not the DataFrame index label.** ``core.taste.attach_match`` merges (and so
    resets) the index and ``sources.discover.build_screenings`` resets it too, so
    a label names a different screening after every reload — a persisted cart
    keyed on one would silently point at other films.

    **Not** :func:`hash`. Python salts string hashing per process, so a cart
    written yesterday would resolve to nothing today.

    Hashed rather than the readable ``film_key|when|theater_id`` triple because of
    where this string ends up. ``ui.ics.to_ics`` writes ``UID:`` **unescaped**
    (``_ics_escape`` only touches SUMMARY/LOCATION/DESCRIPTION) and ``_film_key``
    falls back to a *title*, so a comma, semicolon or newline would emit a
    malformed ICS line; and ``st.container(key=X)`` becomes the CSS class
    ``st-key-X``, which a space would split in two. ``[0-9a-f]`` is inert in both.
    The ingredients stay on :class:`CartItem`, so the JSON on disk is still
    readable and the id re-derivable.

    ``when`` is floored to the minute because the UI renders ``%H:%M``: two
    screenings a user cannot tell apart must not become two cart items.

    ``blake2s`` rather than md5 keeps bandit's B324 out of the conversation; this
    is an identity, not a security boundary.
    """
    payload = f"{film_key}{_SEP}{pd.Timestamp(when).floor('min').isoformat()}{_SEP}{theater_id}"
    return hashlib.blake2s(payload.encode("utf-8"), digest_size=8).hexdigest()


def pick_group_key(ids: Sequence[str]) -> str:
    """Widget-key suffix for one pills group: a hash of its *option universe*.

    Keyed on the universe rather than on ``(film, day)`` deliberately, and the
    reason is a silent data-loss bug. Streamlit honours a widget's ``default=``
    only on the run where its key is absent from session state, and prunes a
    stored selection when ``options`` shrink. With a fixed key: pick 19:00 →
    tighten a filter so that showtime leaves the group (the cart correctly keeps
    it, the widget prunes it) → relax the filter again → the key still exists, so
    ``default=`` is ignored, the widget reports the pruned selection, and
    reconciliation deletes the pick the user never touched.

    Rehashing means any change to the option set mounts a *new* widget, whose
    ``default=`` reseeds from the cart. **The cart is the source of truth; the
    widget is a view of it**, and this function is what enforces that. It also
    matches ``st.pills``' own semantics: ``persist_state`` defaults to ``None``,
    i.e. the value is discarded once the widget stops rendering, so a fresh mount
    seeded from the cart is the intended path rather than a workaround.

    The superseded key is simply never read again (Streamlit garbage-collects
    unrendered widget state; were that to change it would leak a few bytes of
    session state and nothing else). ``ui.cart`` sweeps them by prefix.
    """
    return hashlib.blake2s(_SEP.join(ids).encode("utf-8"), digest_size=8).hexdigest()


def _jsonable(value: object) -> object:
    """Coerce one snapshot value to something JSON round-trips faithfully.

    NaN/NaT become ``None``; numpy scalars become Python scalars; ``pd.Timestamp``
    is left for ``json.dump(default=str)``.

    Not cosmetic. ``json.dump(..., default=str)`` renders a float NaN as the
    *string* ``"nan"``, which is truthy — so ``ui.ics._summary_of``'s
    ``row.get("letterboxd_title") or row.get("french_title") or "Screening"``
    chain would stop at it and export a film called "nan" instead of falling
    through to the French title.
    """
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value
    # numpy int64/float64/bool_ are not JSON-serialisable; .item() unwraps them.
    item = getattr(value, "item", None)
    return item() if callable(item) and hasattr(value, "dtype") else value


def snapshot(row: Mapping[str, object] | pd.Series) -> dict[str, object]:
    """The :data:`CART_SNAPSHOT_FIELDS` of one screening row, JSON-safe.

    A snapshot, and it stays one. CLAUDE.md's "anything persisting a row snapshot
    must re-resolve it at render time" rule (``chat.pins.resolve_pin``) does **not**
    transfer here, for two reasons worth stating so nobody bolts it on: a pin is a
    *film* kept indefinitely, whose frozen shape drifts as cache columns are added
    over months, whereas a cart item is a *screening* pruned the moment it starts,
    so its snapshot is at most about a week old — the horizon of the data itself.
    And re-resolving would be actively worse: the only frame available at render
    time is the day/lens-scoped one, so an item would show a poster when its day
    is selected and none otherwise.

    What replaces re-resolution is this explicit field list plus a test asserting
    it covers everything the ICS builders read.
    """
    return {field: _jsonable(row.get(field)) for field in CART_SNAPSHOT_FIELDS}


@dataclasses.dataclass(frozen=True)
class CartItem:
    """One picked screening: its stable id, that id's ingredients, its payload."""

    id: str
    film_key: str
    when: pd.Timestamp
    fields: dict[str, object]

    @property
    def title(self) -> str:
        """Display title, matching ``ui.ics._summary_of``'s fallback chain."""
        return str(self.fields.get("letterboxd_title") or self.fields.get("french_title") or "Screening")

    @property
    def theater(self) -> str:
        return str(self.fields.get("theater_name") or "")


@dataclasses.dataclass
class ScreeningCart:
    """The picked screenings, keyed by :func:`showtime_id`.

    A dict rather than a list: membership is O(1) (the pills reseed from it on
    every render), and picking the same screening twice is a no-op for free.
    """

    items: dict[str, CartItem] = dataclasses.field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.items)

    def sorted_items(self) -> list[CartItem]:
        """Cart contents in chronological order — never insertion (pick) order.

        A plan is read as a timeline; the order in which the films were ticked is
        not information anyone wants back. ``id`` breaks ties so the export is
        byte-stable across runs.
        """
        return sorted(self.items.values(), key=lambda item: (item.when, item.id))


def cart_index(df: pd.DataFrame) -> dict[str, CartItem]:
    """Every screening in ``df`` as a :class:`CartItem`, keyed by :func:`showtime_id`.

    The id and the payload are produced from the same row in the same pass, which
    is precisely why this — and not the renderer — is the only place a snapshot is
    built: the two are structurally incapable of describing different screenings.

    Applies :func:`~core.agenda.with_agenda_columns` (documented idempotent) so a
    caller may hand over a pre- or post-filter frame, and so ``_dt``/``_film_key``
    are guaranteed present.
    """
    out = with_agenda_columns(df)
    index: dict[str, CartItem] = {}
    for _, row in out.iterrows():
        film_key = str(row.get("_film_key") or "")
        when = row["_dt"]
        sid = showtime_id(film_key, when, str(row.get("theater_id") or ""))
        # First wins: duplicate rows for one film/theater/minute (Allocine emits
        # one per language version, and the SHOWTIMES contract carries no version
        # column) are the same screening as far as a calendar is concerned.
        index.setdefault(sid, CartItem(id=sid, film_key=film_key, when=when, fields=snapshot(row)))
    return index


def entry_showtime_ids(entry: AgendaEntry) -> list[str]:
    """The :func:`showtime_id` of every showtime on one agenda entry, in order.

    De-duplicated, because ``st.pills`` would otherwise be handed two identical
    option values for a film screening twice at one theater in one minute (see
    :func:`cart_index`). Order is ``build_agenda``'s: chronological.
    """
    film_key = str(entry.row.get("_film_key") or "")
    ids = (showtime_id(film_key, show.when, show.theater_id) for show in entry.showtimes)
    return list(dict.fromkeys(ids))


def reconcile_group(
    cart: ScreeningCart,
    universe: Sequence[str],
    selected: Iterable[str],
    index: Mapping[str, CartItem],
) -> bool:
    """Fold one rendered group's selection into the cart: ``(cart - U) | S``.

    Only ids in ``universe`` are ever touched, so a group that is filtered out, on
    another day or behind another lens keeps its cart items *by construction* —
    there is no "which groups did we render?" bookkeeping to get wrong, which is
    the whole reason the cart can outlive the view that built it.

    Returns whether anything changed, so the page writes to disk once per run
    instead of once per group.
    """
    chosen = set(selected)
    changed = False
    for sid in universe:
        if sid in chosen:
            item = index.get(sid)
            # An id with no index entry means the frame moved under us mid-run;
            # dropping the tick beats inventing an item with no payload.
            if item is not None and sid not in cart.items:
                cart.items[sid] = item
                changed = True
        elif cart.items.pop(sid, None) is not None:
            changed = True
    return changed


def cart_frame(cart: ScreeningCart) -> pd.DataFrame:
    """The cart as a screenings frame, **indexed by** :func:`showtime_id`.

    The index label is load-bearing, not incidental: ``ui.ics.build_ics_events``
    derives its ICS ``UID`` as ``f"{idx}-{epoch}@cinema_dashboard"``. Under a fresh
    ``RangeIndex`` a second export would reuse UIDs ``0..n`` for entirely different
    films, and every calendar app would silently overwrite the events imported from
    the first file. A ``showtime_id`` index makes each UID globally stable *and*
    re-importable: exporting the same screening twice updates one event rather than
    duplicating it, which is the correct calendar semantic.

    This is also why ``ui.ics`` needs no change to serve the cart — ``showtime_id``
    is hex-only, so the unescaped ``UID:`` line stays well-formed.
    """
    items = cart.sorted_items()
    return pd.DataFrame(
        [{field: item.fields.get(field) for field in CART_SNAPSHOT_FIELDS} for item in items],
        index=[item.id for item in items],
        columns=list(CART_SNAPSHOT_FIELDS),
    )


def prune_past(cart: ScreeningCart, now: pd.Timestamp) -> int:
    """Drop screenings that have already started; return how many went.

    ``now`` is injected rather than read from a clock here because ``core`` imports
    nothing from ``sources`` — ``ui.cart`` passes ``sources.loader._now_paris()``,
    the single Paris-anchored time source ``future_showtimes`` also uses. A
    tz-aware ``now`` is naive-ised the same way ``future_showtimes`` does it, since
    every showtime in this app is naive Paris wall-clock.

    Compares against the screening's *start*: a film that began ten minutes ago is
    gone from the plan, and one starting in ten minutes is still a legitimate thing
    to export.
    """
    cutoff = now.tz_localize(None) if now.tzinfo is not None else now
    stale = [sid for sid, item in cart.items.items() if item.when < cutoff]
    for sid in stale:
        del cart.items[sid]
    if stale:
        log.info("Pruned %d past screening(s) from the cart", len(stale))
    return len(stale)


# ── Persistence ──────────────────────────────────────────────────────────────
# Mirrors chat.state's disk layer one-for-one: an explicit `path` argument so
# tests pass `tmp_path` instead of patching the constant, and a load that never
# raises — an absent file is the normal first run, and a corrupt one must not
# take the page down with it.


def save_cart(cart: ScreeningCart, path: Path | None = None) -> None:
    """Persist the cart to ``path`` (default :data:`CART_PATH`).

    ``pd.Timestamp`` values go through ``default=str``; :func:`load_cart` re-parses
    them, and ``ui.ics.build_ics_events`` re-parses ``showtimes`` itself, so a
    reloaded cart exports identically to a freshly-picked one.
    """
    path = path or CART_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "items": [
            {"id": item.id, "film_key": item.film_key, "when": item.when, "fields": item.fields} for item in cart.sorted_items()
        ]
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)


def _item_from_json(raw: object) -> CartItem | None:
    """One persisted item back into a :class:`CartItem`, or ``None`` if unusable."""
    if not isinstance(raw, dict):
        return None
    sid, fields, raw_when = raw.get("id"), raw.get("fields"), raw.get("when")
    # `when` is always a string on disk (save_cart's json.dump(default=str)), so
    # anything else is a hand-edited or future-schema file — skip that item.
    if not isinstance(sid, str) or not sid or not isinstance(fields, dict) or not isinstance(raw_when, str):
        return None
    when = pd.to_datetime(raw_when, errors="coerce")
    if not isinstance(when, pd.Timestamp) or pd.isna(when):
        return None
    return CartItem(id=sid, film_key=str(raw.get("film_key") or ""), when=when, fields=fields)


def load_cart(path: Path | None = None) -> ScreeningCart:
    """Return the persisted cart, or an empty one when unavailable.

    Total by design: an absent file (the normal first run), an unreadable or
    corrupt one, and a wrong-shaped one all yield an empty cart, the last two with
    a warning. A single unusable *item* is skipped and the rest kept — a future
    schema addition should cost one screening, not the whole plan.
    """
    path = path or CART_PATH
    if not path.exists():
        return ScreeningCart()
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        raw_items = data.get("items") or []
        if not isinstance(raw_items, list):
            raise ValueError("'items' must be a list")
    except (OSError, ValueError) as exc:  # json.JSONDecodeError subclasses ValueError
        log.warning("Discarding unreadable cart at %s: %s", path, exc)
        return ScreeningCart()

    items: dict[str, CartItem] = {}
    skipped = 0
    for raw in raw_items:
        item = _item_from_json(raw)
        if item is None:
            skipped += 1
            continue
        items[item.id] = item
    if skipped:
        log.warning("Skipped %d unusable cart item(s) in %s", skipped, path)
    return ScreeningCart(items=items)


def delete_cart(path: Path | None = None) -> None:
    """Delete the persisted cart; a missing file is a no-op."""
    (path or CART_PATH).unlink(missing_ok=True)
