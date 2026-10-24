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


def test_secrets_are_read_from_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """In the image, credentials come from /run/secrets files, not the environment."""
    from newgen_ocr.config import get_settings

    (tmp_path / "NGOCR_DATABASE_URL").write_text("postgresql+psycopg://u:hunter2@db:5432/x")
    (tmp_path / "NGOCR_BROKER_URL").write_text("redis://:hunter2@valkey:6379/0")
    monkeypatch.setenv("NGOCR_SECRETS_DIR", str(tmp_path))
    settings = get_settings()
    assert settings.service_mode and settings.database_url is not None
    assert "hunter2" not in repr(settings)
    assert settings.database_url.get_secret_value().endswith("@db:5432/x")
