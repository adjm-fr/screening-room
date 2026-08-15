"""Unit tests for modules/config.py."""

import pytest
from modules.config import Settings, TmdbColumnGroup
from pydantic import ValidationError


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


def test_tmdb_api_key_defaults_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    s = _settings(tmp_path)
    assert s.tmdb_api_key.get_secret_value() == ""


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


def test_tmdb_column_groups_default_is_empty(tmp_path, monkeypatch):
    """No groups set means every column keeps Letterboxd — the pre-migration state."""
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.delenv("TMDB_COLUMN_GROUPS", raising=False)
    s = _settings(tmp_path)
    assert s.tmdb_column_groups == frozenset()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("territories", {TmdbColumnGroup.TERRITORIES}),
        ("genres", {TmdbColumnGroup.GENRES}),
        ("territories,genres", {TmdbColumnGroup.TERRITORIES, TmdbColumnGroup.GENRES}),
        # Whitespace and a trailing comma are what a hand-edited .env actually looks like.
        (" territories , genres , ", {TmdbColumnGroup.TERRITORIES, TmdbColumnGroup.GENRES}),
        ("", set()),
    ],
)
def test_tmdb_column_groups_parses_the_comma_separated_env_form(tmp_path, monkeypatch, raw, expected):
    """pydantic-settings JSON-decodes complex fields by default, which would reject all of
    these — `NoDecode` plus the splitting validator is what makes the plain env form work.
    """
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("TMDB_COLUMN_GROUPS", raw)
    s = _settings(tmp_path)
    assert s.tmdb_column_groups == frozenset(expected)


def test_tmdb_column_groups_rejects_an_unknown_group(tmp_path, monkeypatch):
    """A typo'd group must fail loudly rather than silently reverting a migration.

    `extra="ignore"` already swallows a misspelled *key*; a misspelled *value* landing in
    the same silent bucket would mean a run quietly writing Letterboxd values into a cache
    the operator believes is migrated — and only a taste backtest would ever notice.
    """
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "output"))
    monkeypatch.setenv("TMDB_COLUMN_GROUPS", "territorys")
    with pytest.raises(ValidationError):
        _settings(tmp_path)
