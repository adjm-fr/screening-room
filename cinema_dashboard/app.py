"""
Cinema Dashboard — unified Streamlit entry point.

Run with:
    streamlit run app.py
"""

import plotly.io as pio
import streamlit as st
from common import configure_logging
from modules.config import settings
from utils.cmdk import mount_cmdk
from utils.ui import inject_css

configure_logging(settings.log_level, quiet=("httpx", "httpcore", "google_genai", "urllib3"))

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
        st.Page("pages/calendar.py", title="Watchlist Showtimes", icon="📅"),
        st.Page("pages/streaming.py", title="Streaming", icon="📺"),
        st.Page("pages/recommendations.py", title="Recommendations", icon="🤖"),
    ]
)
pg.run()
