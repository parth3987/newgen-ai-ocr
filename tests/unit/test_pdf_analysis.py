from __future__ import annotations

import pytest

from newgen_ocr.ingestion.models import OcrReason
from newgen_ocr.pdf.analysis import (
    PageMeasurements,
    classify_page,
    image_coverage,
    unreliable_char_ratio,
)


def m(
    chars: int = 500, unreliable: float = 0.0, invisible: float = 0.0, images: float = 0.0
) -> PageMeasurements:
    return PageMeasurements(chars, unreliable, invisible, images)


@pytest.mark.parametrize(
    ("measurements", "needs_ocr", "reason"),
    [
        (m(), False, OcrReason.NATIVE_TEXT),
        (m(images=0.2), False, OcrReason.NATIVE_TEXT),  # small logo
        (m(chars=0, images=1.0), True, OcrReason.NO_TEXT_LAYER),
        (m(chars=0), True, OcrReason.NO_TEXT_LAYER),
        (m(chars=5), True, OcrReason.INSUFFICIENT_TEXT),
        (m(unreliable=0.5), True, OcrReason.UNRELIABLE_TEXT),
        (m(invisible=1.0, images=1.0), True, OcrReason.INVISIBLE_TEXT_LAYER),
        (m(images=0.8), True, OcrReason.IMAGE_CONTENT),  # scan with a digital header line
    ],
)
def test_classify_page(measurements: PageMeasurements, needs_ocr: bool, reason: OcrReason) -> None:
    decision = classify_page(measurements, min_chars=20)
    assert decision.needs_ocr is needs_ocr
    assert decision.reason is reason


def test_unreliable_char_ratio() -> None:
    assert unreliable_char_ratio("") == 0.0
    assert unreliable_char_ratio("hello world") == 0.0
    assert unreliable_char_ratio("ab��") == 0.5
    assert unreliable_char_ratio("ab") == 0.5  # private use area
    # Indic scripts are ordinary letters, not "unreliable".
    assert unreliable_char_ratio("తెలుగు हिन्दी தமிழ் ಕನ್ನಡ मराठी") == 0.0


def test_image_coverage() -> None:
    assert image_coverage([], 100, 100) == 0.0
    assert image_coverage([(0, 0, 100, 100)], 100, 100) == 1.0
    assert image_coverage([(0, 0, 50, 100)], 100, 100) == 0.5
    assert image_coverage([(-50, -50, 150, 150)], 100, 100) == 1.0  # clipped to page
    assert image_coverage([(0, 0, 100, 100), (0, 0, 100, 100)], 100, 100) == 1.0  # capped
    assert image_coverage([(200, 200, 300, 300)], 100, 100) == 0.0  # off-page
    assert image_coverage([(0, 0, 10, 10)], 0, 0) == 0.0
