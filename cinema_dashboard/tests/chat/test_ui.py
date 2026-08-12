"""Tests for chat.ui — the pin caption markup.

``render_chat`` itself has no tests: it needs a real Streamlit session and is
covered by manual verification. What is testable here are the two pure markup
builders that live beside it (the house pattern of a builder sitting next to the
renderer that emits it).
"""

from __future__ import annotations

import pandas as pd
import pytest

from chat.ui import _pin_caption_html


def test_pin_caption_formats_the_screening():
    caption = _pin_caption_html({"showtimes": pd.Timestamp("2026-08-04 18:00"), "theater_name": "Le Champo"})

    assert caption == "🎟 Tue 04 Aug · 18:00 — Le Champo"


def test_pin_caption_escapes_the_theater_name():
    caption = _pin_caption_html({"showtimes": "2026-08-04 18:00", "theater_name": "Ciné <b>X</b>"})

    assert "<b>" not in caption
    assert "&lt;b&gt;" in caption


@pytest.mark.parametrize(
    "pinned",
    [
        {},
        {"showtimes": None},
        {"showtimes": pd.NaT},
        {"showtimes": "not-a-date"},
    ],
    ids=["absent", "none", "nat", "unparseable"],
)
def test_pin_caption_is_empty_without_a_usable_date(pinned):
    assert _pin_caption_html(pinned) == ""


def test_pin_caption_omits_a_missing_theater():
    assert _pin_caption_html({"showtimes": "2026-08-04 18:00"}) == "🎟 Tue 04 Aug · 18:00"


def test_pin_caption_falls_back_to_the_providers_for_a_streaming_pin():
    """A streaming-only pin has no showtime — the providers are why it was kept."""
    caption = _pin_caption_html({"flatrate": ["netflix"], "free": ["arte"]})

    assert caption == "📺 Netflix · ARTE"


def test_pin_caption_prefers_the_screening_over_the_providers():
    caption = _pin_caption_html({"showtimes": "2026-08-04 18:00", "theater_name": "Le Champo", "flatrate": ["netflix"]})

    assert caption == "🎟 Tue 04 Aug · 18:00 — Le Champo"
