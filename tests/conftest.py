import pytest


@pytest.fixture
def make_movie(mocker):
    """Factory fixture: returns a callable that builds a letterboxdpy Movie mock."""

    def _factory(genres=None, details=None, crew=None):
        m = mocker.MagicMock()
        m.genres = genres or []
        m.details = details or []
        m.crew = crew or {}
        m.id = "id"
        m.url = "url"
        m.imdb_id = None
        m.tmdb_id = None
        m.imdb_link = None
        m.tmdb_link = None
        m.title = "Title"
        m.original_title = None
        m.year = 2020
        m.runtime = 90
        m.tagline = None
        m.description = None
        m.rating = 7.5
        m.poster = None
        m.banner = None
        return m

    return _factory
