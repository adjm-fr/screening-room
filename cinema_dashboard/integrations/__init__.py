"""
External-system integrations: the vendored Allocine theater-search scraper,
the theater-list CSV manager, and the movies_management/Allocine scraper
subprocess wrappers used by ``orchestrate.py`` and ``pipeline/``.

No re-exports — call sites import submodules explicitly
(``from integrations.allocine import search_theaters``,
``from integrations.scrapers import letterboxd_command``).
"""
