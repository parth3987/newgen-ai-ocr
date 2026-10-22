"""Document and page metadata persisted with every ingested document.

These models are the on-disk manifest format and the API response schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from newgen_ocr.imaging.quality import QualityReport
from newgen_ocr.ingestion.formats import DocumentFormat


class PageSource(StrEnum):
    PDF_TEXT = "pdf_text"  # PDF page whose embedded text layer is used directly
    PDF_RENDER = "pdf_render"  # PDF page rendered to an image for OCR
    IMAGE = "image"  # page (or TIFF frame) of an image upload


class OcrReason(StrEnum):
    """Why a page does or does not need OCR."""

    NATIVE_TEXT = "native_text"  # needs_ocr = False
    NO_TEXT_LAYER = "no_text_layer"
    INSUFFICIENT_TEXT = "insufficient_text"
    UNRELIABLE_TEXT = "unreliable_text"
    INVISIBLE_TEXT_LAYER = "invisible_text_layer"
    IMAGE_CONTENT = "image_content"
    IMAGE_INPUT = "image_input"


class BoundingBox(BaseModel):
    """Axis-aligned box, origin at the page's top-left corner."""

    x0: float
    y0: float
    x1: float
    y1: float


class TextSegment(BaseModel):
    text: str
    bbox: BoundingBox


class PageText(BaseModel):
    """Embedded PDF text for one page, with segment-level source evidence."""

    page_number: int
    units: Literal["pt"] = "pt"
    page_width: float
    page_height: float
    text: str
    segments: list[TextSegment]


class PageImageInfo(BaseModel):
    width_px: int
    height_px: int
    dpi: int | None  # render DPI for PDF pages; None when unknown for image uploads


class PageManifest(BaseModel):
    page_number: int  # 1-based, in document order
    source: PageSource
    needs_ocr: bool
    ocr_reason: OcrReason
    width_pt: float | None = None
    height_pt: float | None = None
    rotation: int = 0
    embedded_text_chars: int = 0
    has_embedded_text: bool = False
    image: PageImageInfo | None = None
    quality: QualityReport | None = None  # assessed for every page that has an image


class ProcessingInfo(BaseModel):
    pipeline_version: str
    render_dpi: int
    native_text_min_chars: int
    pdfium_version: str | None
    pillow_version: str
    opencv_version: str


class DocumentManifest(BaseModel):
    document_id: str
    created_at: datetime
    sha256: str
    size_bytes: int
    format: DocumentFormat
    media_type: str
    original_filename: str | None
    declared_content_type: str | None
    pdf_encrypted: bool = False  # permissions-only encryption; password-protected is rejected
    page_count: int
    pages: list[PageManifest]
    processing: ProcessingInfo
