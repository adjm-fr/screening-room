"""
Inject both source project roots into sys.path so that dashboard pages can
import from movies_management and Allocine-Showtimes-Scraping without
installing them as packages.

Import this module before any cross-project import:
    import lib.path_setup  # noqa: F401
"""

import sys
from pathlib import Path

_github = Path(__file__).resolve().parents[2]  # .../Developer/github/

for _project in ("movies_management", "Allocine-Showtimes-Scraping"):
    _p = str(_github / _project)
    if _p not in sys.path:
        sys.path.insert(0, _p)
