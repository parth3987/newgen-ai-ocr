from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from newgen_ocr.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NGOCR_ENVIRONMENT", "NGOCR_LOG_LEVEL", "NGOCR_LOG_FORMAT", "NGOCR_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.environment == "development"
    assert s.log_level == "INFO"
    assert s.log_format == "json"
    assert s.data_dir == Path("./data")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NGOCR_ENVIRONMENT", "production")
    monkeypatch.setenv("NGOCR_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("NGOCR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NGOCR_DATABASE_URL", "postgresql+psycopg://u:secret@db:5432/ngocr")
    monkeypatch.setenv("NGOCR_BROKER_URL", "redis://:secret@valkey:6379/0")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.environment == "production"
    assert s.log_level == "WARNING"
    assert s.data_dir == tmp_path


def test_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGOCR_ENVIRONMENT", "prod-ish")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_are_immutable() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        s.log_level = "DEBUG"  # type: ignore[misc]
