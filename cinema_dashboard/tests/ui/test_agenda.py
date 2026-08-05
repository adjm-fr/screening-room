"""Tests for ui.agenda — agenda day/row HTML (no Streamlit context required).

The renderers take :class:`core.agenda.AgendaDay` values, so these tests build
them directly rather than going through ``build_agenda`` — that grouping is
covered in ``tests/core/test_agenda.py``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from core.agenda import AgendaDay, AgendaEntry, AgendaShowtime
from core.taste import TasteProfile
from ui.agenda import _agenda_row_html, agenda_day_html, render_agenda


def _make_profile() -> TasteProfile:
    affinities = {"directors": {"Alfred Hitchcock": 0.9}, "genres": {"Western": 0.5}}
    return TasteProfile(
        mu=3.0,
        n_ratings=10,
        affinities=affinities,
        counts={dim: {value: 10 for value in values} for dim, values in affinities.items()},
    )


def _entry(*, showtimes: list[tuple[str, str]] | None = None, **overrides) -> AgendaEntry:
    data = {
        "letterboxd_slug": "vertigo",
        "letterboxd_title": "Vertigo",
        "directors": "Alfred Hitchcock",
        "runtime_minutes": 128.0,
        "letterboxd_avg_rating": 4.5,
        "poster_url": "https://img.example/vertigo.jpg",
    }
    data.update(overrides)
    pairs = showtimes or [("2026-08-04 19:00", "Le Champo")]
    stamps = tuple(AgendaShowtime(when=pd.Timestamp(when), theater=theater) for when, theater in pairs)
    return AgendaEntry(row=pd.Series(data), showtimes=stamps, earliest=stamps[0].when, match=data.get("match"))


def _day(entries: list[AgendaEntry], *, label: str = "Tonight", is_today: bool = True) -> AgendaDay:
    return AgendaDay(day=dt.date(2026, 8, 4), label=label, is_today=is_today, entries=tuple(entries))


# ── Day section shell ────────────────────────────────────────────────────────


def test_day_section_carries_label_and_full_date():
    out = agenda_day_html(_day([_entry()]))
    assert 'class="agenda-day' in out
    assert 'class="agenda-day-head"' in out
    assert "Tonight · Tuesday 04 August" in out
    assert out.count("<section") == 1


def test_absolute_day_label_is_not_duplicated():
    """Only the relative labels ("Tonight") get the full date appended."""
    out = agenda_day_html(_day([_entry()], label="Tuesday 04 August", is_today=False))
    assert out.count("Tuesday 04 August") == 1


def test_day_count_is_pluralised():
    assert "1 film<" in agenda_day_html(_day([_entry()]))
    two = _day([_entry(), _entry(letterboxd_slug="mandy", letterboxd_title="Mandy")])
    assert "2 films<" in agenda_day_html(two)


def test_today_modifier_only_when_today():
    assert "agenda-day--today" in agenda_day_html(_day([_entry()], is_today=True))
    assert "agenda-day--today" not in agenda_day_html(_day([_entry()], is_today=False))


def test_one_row_per_entry():
    day = _day([_entry(), _entry(letterboxd_slug="mandy", letterboxd_title="Mandy")])
    assert agenda_day_html(day).count('class="agenda-row') == 2


# ── Linking ──────────────────────────────────────────────────────────────────


def test_row_with_slug_links_to_the_detail_page():
    out = _agenda_row_html(_entry())
    assert "agenda-row--linked" in out
    assert '<a class="movie-card-link" href="?movie=vertigo" target="_self">' in out


def test_row_without_slug_is_not_linked():
    out = _agenda_row_html(_entry(letterboxd_slug=None))
    assert "agenda-row--linked" not in out
    assert "<a " not in out
    assert "Vertigo" in out


def test_row_never_contains_more_than_one_anchor():
    """A second anchor would nest inside the stretched .movie-card-link overlay."""
    row = _entry(trailer_url="https://youtu.be/abc", flatrate=["mubi"])
    assert _agenda_row_html(row, _make_profile()).count("<a ") <= 1


# ── Escaping ─────────────────────────────────────────────────────────────────


def test_title_is_escaped():
    out = _agenda_row_html(_entry(letterboxd_title='Sunset <script>&"'))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_theater_name_is_escaped_inside_the_pill():
    out = _agenda_row_html(_entry(showtimes=[("2026-08-04 19:00", 'Le "Champo"')]))
    assert "&quot;Champo&quot;" in out


def test_poster_url_is_escaped_in_the_src_attribute():
    out = _agenda_row_html(_entry(poster_url='https://img.example/a".jpg'))
    assert 'src="https://img.example/a&quot;.jpg"' in out


# ── Time pills ───────────────────────────────────────────────────────────────


def test_time_pills_one_per_showtime_in_order():
    entry = _entry(showtimes=[("2026-08-04 19:00", "Le Champo"), ("2026-08-04 21:30", "MK2 Bibliothèque")])
    out = _agenda_row_html(entry)
    assert out.count('class="time-pill"') == 2
    assert "19:00" in out and "21:30" in out
    assert out.index("19:00") < out.index("21:30")
    assert '<span class="time-pill-venue">Le Champo</span>' in out


def test_time_pill_without_a_theater_shows_only_the_time():
    out = _agenda_row_html(_entry(showtimes=[("2026-08-04 19:00", "")]))
    assert '<span class="time-pill">19:00</span>' in out
    assert "time-pill-venue" not in out


# ── Poster / meta fallbacks ──────────────────────────────────────────────────


def test_missing_poster_falls_back_to_a_skeleton():
    out = _agenda_row_html(_entry(poster_url=None))
    assert 'class="skeleton agenda-thumb"' in out
    assert "<img" not in out


def test_sub_line_carries_director_and_formatted_runtime():
    out = _agenda_row_html(_entry())
    assert "Alfred Hitchcock" in out
    assert "2h08" in out


def test_missing_runtime_omits_the_segment_rather_than_rendering_a_dash():
    out = _agenda_row_html(_entry(runtime_minutes=None))
    assert "—" not in out
    assert "Alfred Hitchcock" in out


def test_missing_rating_omits_the_chip():
    out = _agenda_row_html(_entry(letterboxd_avg_rating=float("nan")))
    assert "chip--rating" not in out


# ── Match chips ──────────────────────────────────────────────────────────────


def test_match_block_rendered_when_a_profile_and_match_are_present():
    out = _agenda_row_html(_entry(match=84.0), _make_profile())
    assert 'class="agenda-match"' in out
    assert "chip--match" in out
    assert "◎ 84% match" in out


def test_no_match_block_without_a_profile():
    out = _agenda_row_html(_entry(match=84.0))
    assert "agenda-match" not in out


def test_no_match_block_when_the_row_is_unscored():
    out = _agenda_row_html(_entry(), _make_profile())
    assert "agenda-match" not in out


# ── render_agenda ────────────────────────────────────────────────────────────


def test_render_agenda_of_nothing_does_not_raise(mocker):
    markdown = mocker.patch("ui.agenda.st.markdown")
    render_agenda([])
    markdown.assert_not_called()


def test_render_agenda_emits_one_markdown_call_per_day(mocker):
    """One blob per day is what lets the sticky day header have a containing block."""
    markdown = mocker.patch("ui.agenda.st.markdown")
    render_agenda([_day([_entry()]), _day([_entry()], label="Tomorrow", is_today=False)])
    assert markdown.call_count == 2
