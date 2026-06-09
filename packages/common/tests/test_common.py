"""Tests for the shared common library."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from common import (
    AppSettings,
    SchemaValidationError,
    configure_logging,
    make_settings_config,
    read_parquet_validated,
    write_parquet_validated,
)


def test_make_settings_config_points_at_env(tmp_path: Path) -> None:
    cfg = make_settings_config(tmp_path)
    assert cfg["env_file"] == tmp_path / ".env"
    assert cfg["extra"] == "ignore"


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
