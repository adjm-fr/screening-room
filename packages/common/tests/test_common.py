"""Tests for the shared common library."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest
from common import AppSettings, configure_logging, make_settings_config
from common.parquet_io import (
    SchemaValidationError,
    read_parquet_validated,
    write_parquet_validated,
)
from common.settings import find_workspace_root


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
