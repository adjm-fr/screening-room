"""Unit tests for modules/config.py."""

from pathlib import Path

import pytest
from modules.config import Settings

_SETTINGS_ENV_VARS = (
    "LOG_LEVEL",
    "MOVIES_OUTPUT_PATH",
    "ALLOCINE_OUTPUT_PATH",
    "ALLOCINE_INPUT_PATH",
    "ALLOCINE_DIR",
    "MOVIES_DIR",
    "LETTERBOXD_USERNAME",
    "LETTERBOXD_DAYS_TO_UPDATE",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GEMINI_MAX_TOKENS",
    "GEMINI_TEMPERATURE",
    "GEMINI_TOP_P",
    "TMDB_API_KEY",
    "STREAMING_SERVICES",
)


@pytest.fixture(autouse=True)
def _clear_settings_env(monkeypatch):
    # Importing deepeval triggers load_dotenv(), which leaks the repo's .env
    # into os.environ. Scrub the Settings-backed vars so each test starts clean.
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _settings(tmp_path, **env_overrides):
    """Instantiate Settings with a blank env file so only env vars from the test apply."""
    return Settings(_env_file=str(tmp_path / "nonexistent.env"), **env_overrides)  # type: ignore[call-arg]


def test_all_paths_default_to_none(tmp_path):
    s = _settings(tmp_path)
    assert s.movies_output_path is None
    assert s.allocine_output_path is None
    assert s.allocine_input_path is None


def test_paths_set_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVIES_OUTPUT_PATH", str(tmp_path / "movies"))
    monkeypatch.setenv("ALLOCINE_OUTPUT_PATH", str(tmp_path / "allocine_out"))
    monkeypatch.setenv("ALLOCINE_INPUT_PATH", str(tmp_path / "theaters.csv"))
    s = _settings(tmp_path)
    assert s.movies_output_path == tmp_path / "movies"
    assert s.allocine_output_path == tmp_path / "allocine_out"
    assert s.allocine_input_path == tmp_path / "theaters.csv"


def test_scraper_dir_defaults(tmp_path):
    s = _settings(tmp_path)
    root = Path(__file__).resolve().parents[1]  # cinema_dashboard/
    # movies_management is now an in-repo workspace sibling; Allocine stays a
    # standalone repo *outside* the monorepo, one level further up.
    assert s.movies_dir == root.parent / "movies_management"
    assert s.allocine_dir == root.parent.parent / "Allocine-Showtimes-Scraping"


def test_letterboxd_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.letterboxd_username is None
    assert s.letterboxd_days_to_update == 365


def test_letterboxd_days_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LETTERBOXD_DAYS_TO_UPDATE", "180")
    s = _settings(tmp_path)
    assert s.letterboxd_days_to_update == 180


def test_gemini_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.gemini_api_key is None
    assert s.gemini_model == "gemini-3.1-flash-lite"
    assert s.gemini_max_tokens == 1024


def test_gemini_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_MAX_TOKENS", "512")
    s = _settings(tmp_path)
    assert s.gemini_api_key == "test-key"
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.gemini_max_tokens == 512


def test_log_level_defaults_to_info(tmp_path):
    s = _settings(tmp_path)
    assert s.log_level == "INFO"


def test_log_level_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    s = _settings(tmp_path)
    assert s.log_level == "DEBUG"


def test_extra_env_vars_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("UNKNOWN_VAR", "should_be_ignored")
    s = _settings(tmp_path)
    assert not hasattr(s, "unknown_var")
