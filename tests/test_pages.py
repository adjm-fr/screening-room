"""Tests for env-var helper functions in showtimes.py and database.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


class TestShowtimesPath:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("ALLOCINE_OUTPUT_PATH", raising=False)
        from pages.showtimes import _showtimes_path

        assert _showtimes_path() is None

    def test_returns_path_when_set(self, monkeypatch, tmp_path):
        target = str(tmp_path / "showtimes.parquet")
        monkeypatch.setenv("ALLOCINE_OUTPUT_PATH", target)
        from pages.showtimes import _showtimes_path

        assert _showtimes_path() == Path(target)


class TestDatabaseOutputPath:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("MOVIES_OUTPUT_PATH", raising=False)
        from pages.database import _output_path

        assert _output_path() is None

    def test_returns_path_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MOVIES_OUTPUT_PATH", str(tmp_path))
        from pages.database import _output_path

        assert _output_path() == tmp_path
