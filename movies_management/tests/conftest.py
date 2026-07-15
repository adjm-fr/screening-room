import pytest


@pytest.fixture(autouse=True)
def _instant_retries():
    """Strip tenacity's backoff so retry paths don't add real delay under filterwarnings=error.

    Each tenacity-decorated function exposes its retrying object as ``.retry``; overriding
    its ``sleep`` makes every wait a no-op (async no-op for the AsyncRetrying case).
    """
    from modules.allocine_enrichment import _search_films
    from modules.get_letterboxd_data import _build_movie, _get_tmdb_credits, _get_tmdb_movie, _get_tmdb_videos

    async def _async_noop(*_args, **_kwargs):
        return None

    _build_movie.retry.sleep = lambda *_a, **_k: None
    _search_films.retry.sleep = lambda *_a, **_k: None
    _get_tmdb_movie.retry.sleep = _async_noop
    _get_tmdb_credits.retry.sleep = _async_noop
    _get_tmdb_videos.retry.sleep = _async_noop
    yield


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
