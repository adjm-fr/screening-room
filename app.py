"""
Cinema Dashboard — unified Streamlit entry point.

Run with:
    streamlit run app.py
"""

import logging

import plotly.io as pio
import streamlit as st

from utils.cmdk import mount_cmdk
from utils.ui import inject_css

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

st.set_page_config(
    page_title="Cinema Dashboard",
    layout="wide",
    page_icon="🎬",
)

# Cinema theme is set in .streamlit/config.toml; CSS layer adds editorial
# typography, movie cards, poster rails, chips, KPI cards, and motion. Plotly
# follows the dark base so its charts blend with the rest of the page.
inject_css()
pio.templates.default = "plotly_dark"

mount_cmdk()

pg = st.navigation(
    [
        st.Page("pages/0_home.py", title="Home", icon="🏠", default=True),
        st.Page("pages/database.py", title="Movies Database", icon="📊"),
        st.Page("pages/calendar.py", title="Watchlist Calendar", icon="📅"),
        st.Page("pages/recommendations.py", title="Recommendations", icon="🤖"),
    ]
)
pg.run()
