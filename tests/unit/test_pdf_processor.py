from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import OcrReason, PageSource
from newgen_ocr.pdf.processor import (
    PdfLimits,
    ProcessedPage,
    check_pdf,
    is_encrypted,
    open_pdf,
    process_pages,
)
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf, jpeg_bytes

LIMITS = PdfLimits(
    max_pages=50, max_page_pixels=80_000_000, render_dpi=144, native_text_min_chars=20
)


def _write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "doc.pdf"
    path.write_bytes(data)
    return path


def _process(tmp_path: Path, data: bytes, limits: PdfLimits = LIMITS) -> list[ProcessedPage]:
    with open_pdf(_write(tmp_path, data)) as pdf:
        check_pdf(pdf, limits)
        return list(process_pages(pdf, limits))


def test_native_text_page_is_not_rendered(tmp_path: Path) -> None:
    [page] = _process(tmp_path, build_pdf([PageSpec(PARAGRAPH)]))
    assert page.manifest.needs_ocr is False
    assert page.manifest.source is PageSource.PDF_TEXT
    assert page.manifest.ocr_reason is OcrReason.NATIVE_TEXT
    assert page.image is None
    assert page.manifest.image is None
    assert page.text is not None
    assert "Payment is due within thirty days" in page.text.text


def test_text_segments_carry_bounding_boxes(tmp_path: Path) -> None:
    [page] = _process(tmp_path, build_pdf([PageSpec(PARAGRAPH)]))
    assert page.text is not None
    segments = page.text.segments
    assert [s.text.strip() for s in segments] == PARAGRAPH.split("\n")
    first = segments[0].bbox
    # Text starts at x=72pt, baseline 72pt below the top edge (top-left origin).
    assert 70 <= first.x0 <= 75
    assert 55 <= first.y0 < first.y1 <= 76
    # Lines are top-to-bottom.
    assert [s.bbox.y0 for s in segments] == sorted(s.bbox.y0 for s in segments)


def test_scanned_page_is_rendered_at_configured_dpi(tmp_path: Path) -> None:
    [page] = _process(tmp_path, build_pdf([PageSpec(jpeg=jpeg_bytes((200, 100)))]))
    assert page.manifest.needs_ocr is True
    assert page.manifest.source is PageSource.PDF_RENDER
    assert page.manifest.ocr_reason is OcrReason.NO_TEXT_LAYER
    assert page.text is None
    image = page.image
    assert isinstance(image, Image.Image)
    # Letter page (612x792 pt) at 144 DPI = 2x.
    assert image.size == (1224, 1584)
    assert page.manifest.image is not None
    assert (page.manifest.image.width_px, page.manifest.image.dpi) == (1224, 144)
    # The embedded image (left half black, right half white) is stretched over the page.
    gray = image.convert("L")
    assert gray.getpixel((300, 800)) < 40
    assert gray.getpixel((900, 800)) > 215


def test_invisible_ocr_layer_is_kept_but_page_still_needs_ocr(tmp_path: Path) -> None:
    data = build_pdf([PageSpec(PARAGRAPH, jpeg=jpeg_bytes(), invisible_text=True)])
    [page] = _process(tmp_path, data)
    assert page.manifest.needs_ocr is True
    assert page.manifest.ocr_reason is OcrReason.INVISIBLE_TEXT_LAYER
    assert page.image is not None
    assert page.text is not None  # embedded third-party OCR text retained for comparison
    assert page.manifest.has_embedded_text is True


def test_multipage_mixed_document_preserves_order(tmp_path: Path) -> None:
    specs = []
    for n in range(1, 7):
        if n % 2:
            specs.append(PageSpec(f"Page {n} marker text that is long enough to count"))
        else:
            # Distinct page widths identify scanned pages after rendering.
            specs.append(PageSpec(jpeg=jpeg_bytes(), size=(500 + n, 700)))
    pages = _process(tmp_path, build_pdf(specs))
    assert [p.manifest.page_number for p in pages] == [1, 2, 3, 4, 5, 6]
    for page in pages:
        n = page.manifest.page_number
        if n % 2:
            assert page.manifest.needs_ocr is False
            assert page.text is not None
            assert page.text.text.startswith(f"Page {n} marker")
        else:
            assert page.manifest.needs_ocr is True
            assert page.manifest.width_pt == 500 + n


def test_owner_password_only_pdf_is_accepted(tmp_path: Path) -> None:
    with open_pdf(_write(tmp_path, build_pdf([PageSpec(PARAGRAPH)], owner_password="o"))) as pdf:
        assert is_encrypted(pdf) is True
        [page] = list(process_pages(pdf, LIMITS))
    assert page.text is not None
    assert "invoice" in page.text.text


def test_unencrypted_pdf_reports_not_encrypted(tmp_path: Path) -> None:
    with open_pdf(_write(tmp_path, build_pdf([PageSpec(PARAGRAPH)]))) as pdf:
        assert is_encrypted(pdf) is False


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (build_pdf([PageSpec(PARAGRAPH)], user_password="secret"), ErrorCode.ENCRYPTED_DOCUMENT),
        (b"%PDF-1.4\nnot really a pdf", ErrorCode.CORRUPT_DOCUMENT),
        (build_pdf([PageSpec(PARAGRAPH)])[:300], ErrorCode.CORRUPT_DOCUMENT),
        (build_pdf([]), ErrorCode.CORRUPT_DOCUMENT),
    ],
)
def test_rejected_pdfs(tmp_path: Path, data: bytes, code: ErrorCode) -> None:
    with pytest.raises(AppError) as exc:
        _process(tmp_path, data)
    assert exc.value.code is code


def test_too_many_pages(tmp_path: Path) -> None:
    limits = PdfLimits(max_pages=2, max_page_pixels=10**8, render_dpi=72, native_text_min_chars=1)
    with pytest.raises(AppError) as exc:
        _process(tmp_path, build_pdf([PageSpec("a"), PageSpec("b"), PageSpec("c")]), limits)
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES


def test_oversized_page_is_rejected_before_rendering(tmp_path: Path) -> None:
    huge = PageSpec(jpeg=jpeg_bytes(), size=(14400, 14400))  # 200 x 200 inch
    with pytest.raises(AppError) as exc:
        _process(tmp_path, build_pdf([huge]))
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE
