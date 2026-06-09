"""Tests for utils/allocine_search.py — vendored theater list scraper."""

import json

import pytest
import utils.allocine_search as search_mod


def _make_card(cinema_id: str, name: str, address: str) -> str:
    theater_json = json.dumps({"id": cinema_id, "name": name})
    return f"""
    <div class="theater-card">
        <button class="add-theater-anchor" data-theater='{theater_json}'></button>
        <address>{address}</address>
    </div>
    """


def _make_page(cards: list[str], has_next: bool) -> str:
    if has_next:
        next_btn = '<button class="button-right">›</button>'
    else:
        next_btn = '<button class="button-right button-disabled">›</button>'
    return f"<html><body>{''.join(cards)}{next_btn}</body></html>"


@pytest.fixture(autouse=True)
def reset_cache():
    search_mod._paris_cinemas = None
    yield
    search_mod._paris_cinemas = None


def _mock_get(mocker, html: str):
    mock_resp = mocker.Mock()
    mock_resp.text = html
    mock_resp.raise_for_status = mocker.Mock()
    return mocker.patch("utils.allocine_search.requests.get", return_value=mock_resp)


# --- _fetch_cinemas_page ---


def test_fetch_single_page_returns_cinemas(mocker):
    html = _make_page([_make_card("C0001", "Le Médicis", "4 rue Médicis")], has_next=False)
    _mock_get(mocker, html)

    cinemas, has_next = search_mod._fetch_cinemas_page("ville-115755", 1)

    assert cinemas == [{"id": "C0001", "name": "Le Médicis", "address": "4 rue Médicis"}]
    assert has_next is False


def test_fetch_detects_next_page(mocker):
    html = _make_page([_make_card("C0002", "Studio 28", "10 rue Tholozé")], has_next=True)
    _mock_get(mocker, html)

    _, has_next = search_mod._fetch_cinemas_page("ville-115755", 1)

    assert has_next is True


def test_fetch_skips_card_without_anchor(mocker):
    html = "<html><body><div class='theater-card'><address>Somewhere</address></div></body></html>"
    _mock_get(mocker, html)

    cinemas, _ = search_mod._fetch_cinemas_page("ville-115755", 1)

    assert cinemas == []


# --- _get_paris_cinemas ---


def test_get_paris_cinemas_paginates(mocker):
    page1 = _make_page([_make_card("C0001", "Cinéma A", "1 rue A")], has_next=True)
    page2 = _make_page([_make_card("C0002", "Cinéma B", "2 rue B")], has_next=False)

    responses = []
    for html in [page1, page2]:
        resp = mocker.Mock()
        resp.text = html
        resp.raise_for_status = mocker.Mock()
        responses.append(resp)
    mocker.patch("utils.allocine_search.requests.get", side_effect=responses)

    result = search_mod._get_paris_cinemas()

    assert [c["id"] for c in result] == ["C0001", "C0002"]


def test_get_paris_cinemas_cached_on_second_call(mocker):
    html = _make_page([_make_card("C0001", "Cinéma A", "1 rue A")], has_next=False)
    get_mock = _mock_get(mocker, html)

    search_mod._get_paris_cinemas()
    search_mod._get_paris_cinemas()

    assert get_mock.call_count == 1


# --- search_theaters ---


def test_search_returns_matching_cinema(mocker):
    mocker.patch(
        "utils.allocine_search._get_paris_cinemas",
        return_value=[{"id": "C0159", "name": "Le Médicis", "address": "4 rue Médicis"}],
    )
    assert search_mod.search_theaters("medicis")[0]["id"] == "C0159"


def test_search_accent_insensitive(mocker):
    mocker.patch(
        "utils.allocine_search._get_paris_cinemas",
        return_value=[{"id": "C0001", "name": "Cinéma des Cinéastes", "address": ""}],
    )
    assert len(search_mod.search_theaters("cineaste")) == 1


def test_search_caps_at_three_results(mocker):
    cinemas = [{"id": f"C{i:04d}", "name": f"Studio {i}", "address": ""} for i in range(5)]
    mocker.patch("utils.allocine_search._get_paris_cinemas", return_value=cinemas)
    assert len(search_mod.search_theaters("studio")) == 3


def test_search_no_match_returns_empty(mocker):
    mocker.patch(
        "utils.allocine_search._get_paris_cinemas",
        return_value=[{"id": "C0001", "name": "Le Grand Rex", "address": ""}],
    )
    assert search_mod.search_theaters("gaumont") == []
