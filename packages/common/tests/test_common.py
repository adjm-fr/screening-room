"""Tests for the shared common library."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import pytest
from common import AppSettings, configure_logging, make_settings_config, reveal, secret_values
from common.logging import RedactingFormatter, redact
from common.parquet_io import (
    SchemaValidationError,
    read_parquet_validated,
    write_parquet_validated,
)
from common.settings import find_workspace_root
from pydantic import SecretStr


def test_make_settings_config_points_at_env(tmp_path: Path) -> None:
    cfg = make_settings_config(tmp_path)
    assert cfg["env_file"] == tmp_path / ".env"
    assert cfg["extra"] == "ignore"


def test_find_workspace_root_locates_uv_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    nested = root / "packages" / "common" / "src" / "common"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n", encoding="utf-8")
    # An intermediate member pyproject without [tool.uv.workspace] must be skipped.
    (root / "packages" / "common" / "pyproject.toml").write_text("[project]\nname = 'common'\n", encoding="utf-8")
    assert find_workspace_root(nested / "settings.py") == root


def test_find_workspace_root_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="workspace root"):
        find_workspace_root(tmp_path / "deep" / "file.py")


def test_make_settings_config_defaults_to_workspace_root() -> None:
    # No argument → the real workspace root's .env (the one this test runs inside).
    assert make_settings_config()["env_file"] == find_workspace_root() / ".env"


def test_app_settings_subclass_reads_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Settings(AppSettings):
        model_config = make_settings_config(tmp_path)
        output_path: Path

    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "out"))
    assert Settings().output_path == tmp_path / "out"


def test_configure_logging_sets_level_and_quiets() -> None:
    configure_logging("DEBUG", quiet=("httpx", "httpcore"))
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    df = pd.DataFrame({"a": [1], "b": [2]})
    path = tmp_path / "nested" / "f.parquet"  # parent created by writer
    write_parquet_validated(df, path, required_columns=["a", "b"])
    out = read_parquet_validated(path, required_columns=["a", "b"])
    assert list(out.columns) == ["a", "b"]


def test_read_validated_raises_on_missing_column(tmp_path: Path) -> None:
    path = tmp_path / "f.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError, match="missing required columns"):
        read_parquet_validated(path, required_columns=["a", "b"], label="showtimes")


def test_write_validated_raises_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "f.parquet"
    with pytest.raises(SchemaValidationError):
        write_parquet_validated(pd.DataFrame({"a": [1]}), path, required_columns=["a", "b"])
    assert not path.exists()


def test_redact_replaces_every_secret() -> None:
    text = redact("key=abc123 and again abc123", "abc123")
    assert "abc123" not in text
    assert text == "key=*** and again ***"


def test_redact_ignores_empty_and_none_secrets() -> None:
    # An unset key must not turn every character into a separator of ``***``.
    assert redact("nothing to hide", None, "") == "nothing to hide"


def _record(msg: str, *, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord("t", logging.WARNING, __file__, 1, msg, None, exc_info)  # type: ignore[arg-type]


def test_redacting_formatter_scrubs_the_message() -> None:
    fmt = RedactingFormatter("%(message)s", secrets=["SECRETKEY"])
    assert fmt.format(_record("url?api_key=SECRETKEY")) == "url?api_key=***"


def test_redacting_formatter_scrubs_the_traceback() -> None:
    """The case a custom exception with a masked ``__str__`` cannot cover.

    ``raise ... from cause`` renders the *cause's* traceback too, so anything that
    only masks the wrapper's own message still leaks the original text.
    """
    try:
        try:
            raise ValueError("url?api_key=SECRETKEY")
        except ValueError as cause:
            raise RuntimeError("wrapped") from cause
    except RuntimeError:
        record = _record("boom", exc_info=sys.exc_info())

    rendered = RedactingFormatter("%(message)s", secrets=["SECRETKEY"]).format(record)
    assert "Traceback" in rendered, "expected the traceback to be rendered at all"
    assert "SECRETKEY" not in rendered


def test_redacting_formatter_without_secrets_is_a_passthrough() -> None:
    fmt = RedactingFormatter("%(message)s", secrets=[None, ""])
    assert fmt.format(_record("nothing to hide")) == "nothing to hide"


def test_configure_logging_installs_the_redacting_formatter() -> None:
    """The guarantee: no call site has to remember anything."""
    configure_logging("INFO", secrets=["SECRETKEY"])
    handlers = logging.getLogger().handlers
    assert handlers, "expected at least one root handler"
    for handler in handlers:
        assert isinstance(handler.formatter, RedactingFormatter)
        assert "SECRETKEY" not in handler.formatter.format(_record("api_key=SECRETKEY"))


def test_reveal_unwraps_a_secret() -> None:
    assert reveal(SecretStr("abc123")) == "abc123"


def test_reveal_passes_none_through() -> None:
    """An unset optional key stays None rather than becoming the string "None"."""
    assert reveal(None) is None


def test_empty_secret_is_falsy() -> None:
    """``if not settings.tmdb_api_key`` guards depend on this (see orchestrate.py).

    Pydantic keeps ``SecretStr`` truthiness tied to the wrapped value, so swapping a
    plain ``str`` field for a secret one does not silently invert those checks.
    """
    assert not SecretStr("")
    assert SecretStr("x")


class _KeyedSettings(AppSettings):
    """Stands in for a member's Settings: two credentials plus ordinary config."""

    tmdb_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    log_level: str = "INFO"


def test_secret_values_collects_every_secret_field() -> None:
    s = _KeyedSettings(tmdb_api_key=SecretStr("AAA"), gemini_api_key=SecretStr("BBB"))
    assert sorted(secret_values(s)) == ["AAA", "BBB"]


def test_secret_values_ignores_non_secret_fields() -> None:
    """A log_level of "INFO" must never be scrubbed out of the messages it configures."""
    s = _KeyedSettings(tmdb_api_key=SecretStr("AAA"))
    assert secret_values(s) == ["AAA"]


def test_secret_values_skips_unset_and_empty_secrets() -> None:
    assert secret_values(_KeyedSettings()) == []
    assert secret_values(_KeyedSettings(tmdb_api_key=SecretStr(""))) == []


def test_secret_values_picks_up_a_newly_declared_key_on_its_own() -> None:
    """The reason this exists: a hand-written list at each entry point goes stale.

    Adding a credential to Settings must protect it without anyone remembering to
    register it somewhere — nothing would fail or warn if they forgot, the value
    would just start appearing in the logs.
    """

    class _WithNewKey(_KeyedSettings):
        brand_new_api_key: SecretStr | None = None

    s = _WithNewKey(tmdb_api_key=SecretStr("AAA"), brand_new_api_key=SecretStr("CCC"))
    assert "CCC" in secret_values(s)
