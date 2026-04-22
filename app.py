"""
Cinema Dashboard — unified Streamlit entry point.

Run with:
    streamlit run app.py
"""

import logging

import streamlit as st

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

pg = st.navigation(
    [
        st.Page("pages/showtimes.py", title="Showtimes", icon="🎟️"),
        st.Page("pages/database.py", title="Movies Database", icon="📊"),
        st.Page("pages/calendar.py", title="Watchlist Calendar", icon="📅"),
        st.Page("pages/recommendations.py", title="Recommendations", icon="🤖"),
    ]
)
pg.run()
