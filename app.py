"""
Cinema Dashboard — unified Streamlit entry point.

Combines:
- Allocine showtimes scraping (pages/showtimes.py)
- movies_management database stats (pages/database.py)
- Letterboxd watchlist × showtimes calendar (pages/calendar.py)

Run with:
    streamlit run app.py
"""

import sys
from pathlib import Path

# Ensure source project roots are on sys.path before any cross-project imports
sys.path.insert(0, str(Path(__file__).parent))
import streamlit as st

import lib.path_setup  # noqa: F401

st.set_page_config(
    page_title="Cinema Dashboard",
    layout="wide",
    page_icon="🎬",
)

pg = st.navigation(
    [
        st.Page("pages/showtimes.py", title="Showtimes", icon="🎟️"),
        st.Page("pages/database.py", title="Movies Database", icon="📊"),
        st.Page("pages/calendar.py", title="Watchlist Calendar", icon="📅"),
    ]
)
pg.run()
