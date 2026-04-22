"""
Read and append entries to the theaters CSV used by Allocine-Showtimes-Scraping.

The theaters CSV is the input file read by the scraper (ALLOCINE_INPUT_PATH in .env).
Its format is three columns with no header:

    theater_id,theater_name,address
    C0073,Le Champo - Espace Jacques Tati,51 rue des Ecoles 75005 Paris
    C0159,UGC Ciné Cité Les Halles,7 Place de la Rotonde 75001 Paris

The third column (address) is optional — the scraper only reads columns 0 and 1.
It is stored here purely for human readability.

Existing rows that were written before the address column was introduced may have only
two columns. Use backfill_addresses() to enrich them from the Allocine API cache.

This module is intentionally append-only for new entries: it never deletes rows.
backfill_addresses() rewrites the file only to add missing address values.
"""

import csv
from pathlib import Path


def load_theaters(csv_path: Path) -> list[dict]:
    """Return all rows as a list of dicts with keys 'id', 'name', 'address'.

    Rows with fewer than 3 columns will have an empty string for 'address'.
    Returns an empty list if the file doesn't exist yet.
    """
    if not csv_path.exists():
        return []
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            rows.append(
                {
                    "id": row[0].strip(),
                    "name": row[1].strip() if len(row) > 1 else "",
                    "address": row[2].strip() if len(row) > 2 else "",
                }
            )
    return rows


def load_theater_ids(csv_path: Path) -> set[str]:
    """Return the set of theater IDs already present in the CSV."""
    return {t["id"] for t in load_theaters(csv_path)}


def append_theater(csv_path: Path, theater_id: str, theater_name: str, address: str = "") -> bool:
    """Append a theater to the CSV if it isn't already listed.

    Ensures a newline exists before the new row so it always starts on its own line.
    Returns True if the theater was added, False if it was already present (duplicate).
    The caller is responsible for re-running the Allocine scraper afterwards to fetch
    showtimes for the newly added theater.
    """
    if theater_id in load_theater_ids(csv_path):
        return False

    # Ensure the file ends with a newline before appending
    if csv_path.exists():
        content = csv_path.read_bytes()
        if content and not content.endswith(b"\n"):
            with csv_path.open("ab") as f:
                f.write(b"\n")

    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([theater_id, theater_name, address])
    return True


def backfill_addresses(csv_path: Path, cinemas: list[dict]) -> int:
    """Enrich rows that have no address using the provided Allocine cinema list.

    cinemas is the list returned by allocine_search._get_paris_cinemas() —
    each item has 'id', 'name', and 'address'.

    Rewrites the file in-place only if at least one row was updated.
    Returns the number of rows that were updated.
    """
    theaters = load_theaters(csv_path)
    if not theaters:
        return 0

    # Build a lookup from Allocine cinema ID to address
    address_lookup = {c["id"]: c.get("address", "") for c in cinemas}

    updated = 0
    for theater in theaters:
        if not theater["address"] and theater["id"] in address_lookup:
            theater["address"] = address_lookup[theater["id"]]
            updated += 1

    if updated:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for theater in theaters:
                writer.writerow([theater["id"], theater["name"], theater["address"]])

    return updated
