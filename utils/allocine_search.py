"""
Search Paris cinemas via the Allocine API.

This module is used by the Recommendations page to look up theaters when the user
mentions one that isn't already in the showtimes data. The full list of Paris cinemas
is fetched once from the Allocine API and cached in memory for the lifetime of the
Streamlit process — subsequent searches reuse that cached list without hitting the API again.

The Allocine city ID for Paris ('ville-115755') is hardcoded; extend PARIS_VILLE_ID
or add more city IDs if support for other cities is needed.
"""

import unicodedata

from allocineAPI.allocineAPI import allocineAPI

# Allocine's internal identifier for Paris — passed to get_cinema() to list all Paris theaters.
PARIS_VILLE_ID = "ville-115755"

# Module-level API client and cinema cache. The cache is populated on first call to
# _get_paris_cinemas() and reused for all subsequent searches within the same process.
_api = allocineAPI()
_paris_cinemas: list[dict] | None = None


def _get_paris_cinemas() -> list[dict]:
    """Fetch all Paris cinemas from Allocine, caching the result in memory."""
    global _paris_cinemas
    if _paris_cinemas is None:
        _paris_cinemas = _api.get_cinema(PARIS_VILLE_ID)
    return _paris_cinemas


def _normalize(text: str) -> str:
    """Lowercase and strip accents so 'medicis' matches 'Médicis'."""
    return unicodedata.normalize("NFD", text.lower()).encode("ascii", "ignore").decode()


def search_theaters(query: str) -> list[dict]:
    """Return up to 3 Paris cinemas whose name contains query (case- and accent-insensitive).

    Each result is a dict with keys: 'id' (Allocine cinema ID, e.g. 'C0159'),
    'name' (cinema name), and 'address'.
    """
    cinemas = _get_paris_cinemas()
    q = _normalize(query)
    matches = [c for c in cinemas if q in _normalize(c.get("name", ""))]
    return matches[:3]
