"""
Cinema Dashboard — unified Streamlit entry point.

Run with:
    streamlit run app.py

Routing has two layers. ``st.navigation`` owns the six sections in the
sidebar; on top of that, a ``?movie=<slug>`` query parameter overlays the movie
detail page (``pages/movie.py``) in place of whichever section is selected.
Every movie card in the app is an anchor to that URL (:func:`ui.movie_href`),
so films get real, shareable, back-button-friendly links from any page without
each page having to know about detail routing. The detail module is *called*
rather than mounted as an ``st.Page``: ``StreamlitPage.run()`` only works on the
page ``st.navigation`` itself returns, and a detail view that shares a URL path
with every section has no page of its own to be routed to.
"""

import plotly.io as pio
import streamlit as st
from common import configure_logging

from config import settings
from pages.movie import main as render_movie_detail
from ui import MOVIE_QUERY_PARAM, inject_css
from ui.cmdk import mount_cmdk

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
        st.Page("pages/paris.py", title="Screening in Paris", icon="🎭"),
        st.Page("pages/streaming.py", title="Streaming", icon="📺"),
        st.Page("pages/recommendations.py", title="Recommendations", icon="🤖"),
    ]
)

# A `movie` parameter present but empty (`?movie=`) still routes to the detail
# page, which renders its "no film at this link" empty state — a truncated or
# mistyped link should explain itself rather than silently land on Home.
movie_slug = st.query_params.get(MOVIE_QUERY_PARAM)
if movie_slug is None:
    pg.run()
else:
    render_movie_detail(movie_slug)
