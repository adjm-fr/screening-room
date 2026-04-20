"""
Cinema Dashboard — unified Streamlit entry point.

Run with:
    streamlit run app.py
"""

import streamlit as st

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
