"""Decide per PDF page whether its embedded text can be used or the page needs OCR.

Pure functions over measured page properties, so the policy is unit-testable
without PDFs. Thresholds are documented in docs/DECISIONS.md (ADR-0011).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from newgen_ocr.ingestion.models import OcrReason

# Text objects drawn invisibly (render mode 3) are typically a third-party OCR layer.
INVISIBLE_TEXT_THRESHOLD = 0.9
# Fraction of characters that are replacement/control/private-use, which indicates
# fonts without a usable Unicode mapping.
UNRELIABLE_TEXT_THRESHOLD = 0.1
# Fraction of the page covered by raster images above which the page is treated as
# containing image content that text extraction cannot see.
IMAGE_COVERAGE_THRESHOLD = 0.5


@dataclass(frozen=True)
class PageMeasurements:
    char_count: int  # non-whitespace characters in the text layer
    unreliable_char_ratio: float
    invisible_text_ratio: float
    image_coverage: float


@dataclass(frozen=True)
class PageDecision:
    needs_ocr: bool
    reason: OcrReason


def unreliable_char_ratio(text: str) -> float:
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    bad = sum(1 for ch in chars if ch == "�" or unicodedata.category(ch) in {"Cc", "Co", "Cs"})
    return bad / len(chars)


def image_coverage(
    image_bounds: list[tuple[float, float, float, float]], page_width: float, page_height: float
) -> float:
    """Summed area of image boxes clipped to the page, as a fraction of page area (max 1)."""
    page_area = page_width * page_height
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for left, bottom, right, top in image_bounds:
        width = min(right, page_width) - max(left, 0.0)
        height = min(top, page_height) - max(bottom, 0.0)
        if width > 0 and height > 0:
            covered += width * height
    return min(covered / page_area, 1.0)


def classify_page(m: PageMeasurements, min_chars: int) -> PageDecision:
    if m.char_count == 0:
        return PageDecision(True, OcrReason.NO_TEXT_LAYER)
    if m.invisible_text_ratio >= INVISIBLE_TEXT_THRESHOLD:
        return PageDecision(True, OcrReason.INVISIBLE_TEXT_LAYER)
    if m.unreliable_char_ratio > UNRELIABLE_TEXT_THRESHOLD:
        return PageDecision(True, OcrReason.UNRELIABLE_TEXT)
    if m.char_count < min_chars:
        return PageDecision(True, OcrReason.INSUFFICIENT_TEXT)
    if m.image_coverage >= IMAGE_COVERAGE_THRESHOLD:
        return PageDecision(True, OcrReason.IMAGE_CONTENT)
    return PageDecision(False, OcrReason.NATIVE_TEXT)
