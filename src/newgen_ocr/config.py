"""Application configuration.

All settings come from environment variables prefixed ``NGOCR_`` (optionally via a
local ``.env`` file in development). Secrets must never have defaults here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from newgen_ocr.ingestion.formats import DocumentFormat

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NGOCR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    # Root for local document storage (quarantine, originals, derived artefacts, audit).
    data_dir: Path = Path("./data")

    # Ingestion limits. See docs/SECURITY.md for the rationale behind each.
    allowed_formats: frozenset[DocumentFormat] = frozenset(DocumentFormat)
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=200, gt=0)
    # Upper bound on decoded pixels per page (input images and rendered PDF pages).
    max_page_pixels: int = Field(default=80_000_000, gt=0)

    # PDF processing.
    render_dpi: int = Field(default=300, ge=72, le=600)
    # Minimum extractable characters for a PDF page's text layer to be trusted.
    native_text_min_chars: int = Field(default=20, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once. Tests should pass explicit Settings instead."""
    return Settings()
