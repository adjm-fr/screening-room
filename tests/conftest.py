"""
Pre-import page modules at session start so that load_dotenv() runs once here,
not lazily inside individual tests where it would re-set env vars after
monkeypatch.delenv() has already cleared them.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

with patch("dotenv.load_dotenv"):
    import pages.database  # noqa: F401, E402
    import pages.showtimes  # noqa: F401, E402
