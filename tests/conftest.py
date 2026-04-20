"""
Pre-import calendar page at session start so that load_dotenv() runs once here,
not lazily inside individual tests where it would interfere with monkeypatching.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

with patch("dotenv.load_dotenv"):
    import pages.calendar  # noqa: F401, E402
