"""Application configuration.

All settings come from environment variables prefixed ``NGOCR_`` (optionally via a
local ``.env`` file in development). Secrets must never have defaults here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from newgen_ocr.ingestion.formats import DocumentFormat
from newgen_ocr.preprocessing.profiles import ProfileConfig, merge_profiles

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

    # Preprocessing (docs/DECISIONS.md ADR-0016..0018).
    default_preprocessing_profile: str = "standard"
    # JSON list of additional ProfileConfig objects; names must not clash with built-ins.
    custom_preprocessing_profiles: tuple[ProfileConfig, ...] = ()
    # OpenCV worker threads per process; None keeps OpenCV's default (all cores).
    opencv_threads: int | None = Field(default=None, ge=1, le=256)

    # OCR (docs/DECISIONS.md ADR-0020..0022). Models are read only from models_dir and
    # verified against models_lock; nothing is ever downloaded at runtime.
    models_dir: Path = Path("./models")
    models_lock: Path = Path("./models.lock.json")
    ocr_primary_engine: Literal["paddle", "tesseract"] = "paddle"
    ocr_fallback_engine: Literal["paddle", "tesseract"] | None = "tesseract"
    # Per-language primary engine overrides (the other engine becomes the fallback).
    # Kannada: PaddleOCR's only Kannada model measured CER 0.39 vs Tesseract 0.01 (OD-17).
    ocr_language_primary: dict[str, Literal["paddle", "tesseract"]] = {"kn": "tesseract"}
    ocr_default_languages: tuple[str, ...] = ("en",)
    paddle_detection_model: str = "PP-OCRv5_mobile_det"
    paddle_enable_mkldnn: bool = True
    # CPU threads per OCR engine call; None = engine default.
    ocr_cpu_threads: int | None = Field(default=None, ge=1, le=256)
    tesseract_cmd: str = "tesseract"
    ocr_page_timeout_s: int = Field(default=300, ge=5, le=3600)
    # Pages whose engines agree less than this (1 - CER between them) are flagged for review.
    ocr_disagreement_threshold: float = Field(default=0.85, ge=0.0, le=1.0)

    # Languages and review routing (ADR-0023).
    # Run script/orientation detection (Tesseract OSD, ~1-1.7 s/page) even when languages are
    # given: corrects 90/180/270 pages and flags script mismatches. Always on for "auto".
    ocr_detect_orientation: bool = True
    # Review thresholds. Heuristic, set from the baseline corpus (ACCURACY_EVALUATION.md);
    # confidence does NOT catch a systematically wrong recogniser, so these are one signal
    # among several.
    ocr_min_page_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    ocr_min_line_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    # Languages whose handwriting engine is verified on real samples. Empty: every
    # handwriting result is routed to review.
    ocr_handwriting_verified_languages: tuple[str, ...] = ()

    # Layout and tables (ADR-0025). PP-DocLayout_plus-L: most accurate (~2.2 s/page on CPU);
    # PP-DocLayout-M: ~10x faster, lower region confidence (docs/PERFORMANCE.md).
    layout_model: Literal["PP-DocLayout_plus-L", "PP-DocLayout-M"] = "PP-DocLayout_plus-L"
    # Regions the detector scores below this are discarded (their text becomes unclassified).
    layout_score_threshold: float = Field(default=0.5, ge=0.05, le=0.99)
    # Tables whose SLANet structure score is below this are routed to review.
    layout_min_table_score: float = Field(default=0.90, ge=0.0, le=1.0)
    # Cross-check wired-table structure against traced ruling lines.
    layout_ruling_check: bool = True

    # Structured extraction (ADR-0026). Extra document-type schemas (JSON) are loaded from
    # this directory in addition to the built-ins; ids must not clash.
    extraction_schema_dir: Path | None = None
    # Numeric dates are read day-first (DD/MM/YYYY, the Indian convention). When false, a
    # numeric date valid both ways is reported ambiguous instead.
    extraction_day_first: bool = True
    # Aadhaar numbers are stored and exported masked to the last 4 digits (UIDAI guidance).
    extraction_mask_aadhaar: bool = True
    # Extracted values whose OCR evidence is less confident than this are routed to review.
    extraction_min_evidence_confidence: float = Field(default=0.70, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_ocr_engines(self) -> Settings:
        if self.ocr_fallback_engine == self.ocr_primary_engine:
            raise ValueError("ocr_fallback_engine must differ from ocr_primary_engine")
        return self

    @model_validator(mode="after")
    def _check_profiles(self) -> Settings:
        profiles = merge_profiles(self.custom_preprocessing_profiles)
        if self.default_preprocessing_profile not in profiles:
            raise ValueError(
                f"default_preprocessing_profile {self.default_preprocessing_profile!r} "
                "is not a defined profile"
            )
        return self

    @property
    def preprocessing_profiles(self) -> dict[str, ProfileConfig]:
        return merge_profiles(self.custom_preprocessing_profiles)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once. Tests should pass explicit Settings instead."""
    return Settings()
