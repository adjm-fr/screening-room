"""Unit tests for modules/config.py."""

import pytest
from modules.config import Settings
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _default_tmdb_key(monkeypatch):
    """``tmdb_api_key`` is required now that TMDB is the sole producer of every migrated
    column, so every ``Settings()`` call in this file needs one set. Tests that exercise
    the missing-key path override this with ``monkeypatch.delenv``.
    """
    monkeypatch.setenv("TMDB_API_KEY", "test-key")


def _settings(tmp_path, **env_overrides):
    """Instantiate Settings with a blank env file so only env vars from the test apply."""
    return Settings(_env_file=str(tmp_path / "nonexistent.env"), **env_overrides)  # type: ignore[call-arg]


def test_valid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    s = _settings(tmp_path)
    assert s.output_path == tmp_path / "output"


def test_missing_output_path_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTPUT_PATH", raising=False)
    with pytest.raises(ValidationError):
        _settings(tmp_path)


def test_days_to_update_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    s = _settings(tmp_path)
    assert s.letterboxd_days_to_update == 365


def test_days_to_update_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("LETTERBOXD_DAYS_TO_UPDATE", "180")
    s = _settings(tmp_path)
    assert s.letterboxd_days_to_update == 180


def test_refresh_limit_defaults_to_1000(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    s = _settings(tmp_path)
    assert s.letterboxd_refresh_limit == 1000


def test_refresh_limit_set(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("LETTERBOXD_REFRESH_LIMIT", "50")
    s = _settings(tmp_path)
    assert s.letterboxd_refresh_limit == 50


def test_missing_tmdb_api_key_raises(tmp_path, monkeypatch):
    """TMDB is the sole producer of directors/cast/territories/genres/keywords now, so a
    missing key must fail fast at startup rather than silently nulling those columns.
    """
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        _settings(tmp_path)


def test_tmdb_api_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("TMDB_API_KEY", "abc123")
    s = _settings(tmp_path)
    assert s.tmdb_api_key.get_secret_value() == "abc123"


def test_tmdb_api_key_is_masked_when_printed(tmp_path, monkeypatch):
    """The point of SecretStr: the credential cannot be printed by accident."""
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("TMDB_API_KEY", "abc123")
    s = _settings(tmp_path)
    assert "abc123" not in str(s.tmdb_api_key)
    assert "abc123" not in repr(s.tmdb_api_key)
    assert "abc123" not in f"{s.tmdb_api_key}"
    assert "abc123" not in repr(s)


def test_extra_env_vars_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("LETTERBOXD_USERNAME", "should_be_ignored")
    monkeypatch.setenv("TMDB_API_URL", "should_be_ignored")
    s = _settings(tmp_path)
    assert not hasattr(s, "letterboxd_username")
    assert not hasattr(s, "tmdb_api_url")
