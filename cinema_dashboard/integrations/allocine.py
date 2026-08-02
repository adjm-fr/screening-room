"""
Search Paris cinemas via the Allocine website.

This module is used by the Recommendations page to look up theaters when the user
mentions one that isn't already in the showtimes data. The full list of Paris cinemas
is fetched once and cached in memory for the lifetime of the Streamlit process —
subsequent searches reuse that cached list without hitting the website again.

The Allocine city ID for Paris ('ville-115755') is hardcoded; extend PARIS_VILLE_ID
or add more city IDs if support for other cities is needed.

Theater listing is vendored from allocine-seances 0.0.14 (HTML scraping via
requests + BeautifulSoup), removing the external package dependency.
"""

import json
import unicodedata

import requests
from bs4 import BeautifulSoup

# Allocine's internal identifier for Paris.
PARIS_VILLE_ID = "ville-115755"

_BASE_URL = "https://www.allocine.fr/salle/cinema/"

# Module-level cinema cache — populated on first call to _get_paris_cinemas().
_paris_cinemas: list[dict] | None = None


def _fetch_cinemas_page(location_id: str, page: int) -> tuple[list[dict], bool]:
    """Fetch one page of cinemas for a location and return (cinemas, has_next_page)."""
    url = f"{_BASE_URL}{location_id}/"
    resp = requests.get(url, params={"page": page}, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cinemas = []
    for card in soup.select("[class*='theater-card']"):
        anchor = card.select_one("[class*='add-theater-anchor']")
        if anchor is None:
            continue
        data = json.loads(str(anchor["data-theater"]))
        address_tag = card.find("address")
        cinemas.append(
            {
                "id": data["id"],
                "name": data["name"],
                "address": address_tag.text if address_tag else "",
            }
        )

    buttons = soup.select("[class*='button-right']")
    last_classes = buttons[-1].get("class") if buttons else None
    has_next = bool(buttons) and "button-disabled" not in (last_classes or [])
    return cinemas, has_next


def _get_paris_cinemas() -> list[dict]:
    """Fetch all Paris cinemas from Allocine, caching the result in memory."""
    global _paris_cinemas
    if _paris_cinemas is None:
        result = []
        page = 1
        while True:
            cinemas, has_next = _fetch_cinemas_page(PARIS_VILLE_ID, page)
            result.extend(cinemas)
            if not has_next:
                break
            page += 1
        _paris_cinemas = result
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
