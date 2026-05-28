"""Unit tests for modules/config.py."""

from pathlib import Path

from modules.config import Settings


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
    root = Path(__file__).parent.parent
    assert s.allocine_dir == root.parent / "Allocine-Showtimes-Scraping"
    assert s.movies_dir == root.parent / "movies_management"


def test_letterboxd_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.letterboxd_username is None
    assert s.letterboxd_days_to_update == 365


def test_letterboxd_days_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LETTERBOXD_DAYS_TO_UPDATE", "180")
    s = _settings(tmp_path)
    assert s.letterboxd_days_to_update == 180


def test_hf_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.hf_api_key is None
    assert s.hf_model == "moonshotai/Kimi-K2-Instruct"
    assert s.hf_max_tokens == 1024


def test_hf_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_API_KEY", "test-key")
    monkeypatch.setenv("HF_MODEL", "meta-llama/Llama-3-8B")
    monkeypatch.setenv("HF_MAX_TOKENS", "512")
    s = _settings(tmp_path)
    assert s.hf_api_key == "test-key"
    assert s.hf_model == "meta-llama/Llama-3-8B"
    assert s.hf_max_tokens == 512


def test_extra_env_vars_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("UNKNOWN_VAR", "should_be_ignored")
    s = _settings(tmp_path)
    assert not hasattr(s, "unknown_var")
