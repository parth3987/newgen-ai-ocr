"""PDF validation, text extraction and page rendering with PDFium.

PDFium is not thread-safe, so every use goes through ``PDFIUM_LOCK``. PDFium parses
untrusted input in-process; process isolation is planned (docs/SECURITY.md).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import (
    BoundingBox,
    PageImageInfo,
    PageManifest,
    PageSource,
    PageText,
    TextSegment,
)
from newgen_ocr.pdf.analysis import (
    PageMeasurements,
    classify_page,
    image_coverage,
    unreliable_char_ratio,
)

PDFIUM_LOCK = threading.RLock()
PDFIUM_VERSION = str(pdfium.PDFIUM_INFO)

_PASSWORD_ERRORS = {pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY}


@dataclass(frozen=True)
class PdfLimits:
    max_pages: int
    max_page_pixels: int
    render_dpi: int
    native_text_min_chars: int


@dataclass(frozen=True)
class ProcessedPage:
    manifest: PageManifest
    text: PageText | None
    image: Image.Image | None


@contextmanager
def open_pdf(path: Path) -> Iterator[pdfium.PdfDocument]:
    """Open a PDF under the PDFium lock, mapping load failures to stable error codes."""
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            if exc.err_code in _PASSWORD_ERRORS:
                raise AppError(ErrorCode.ENCRYPTED_DOCUMENT, "PDF is password-protected") from exc
            raise AppError(ErrorCode.CORRUPT_DOCUMENT, "PDF could not be parsed") from exc
        try:
            yield pdf
        finally:
            pdf.close()


def is_encrypted(pdf: pdfium.PdfDocument) -> bool:
    return bool(pdfium_c.FPDF_GetSecurityHandlerRevision(pdf.raw) != -1)


def _render_size(width_pt: float, height_pt: float, dpi: int) -> tuple[int, int]:
    return math.ceil(width_pt * dpi / 72), math.ceil(height_pt * dpi / 72)


def _extract_text(
    textpage: pdfium.PdfTextPage, page_number: int, width: float, height: float
) -> PageText:
    segments = []
    for i in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(i)
        segment_text = textpage.get_text_bounded(left, bottom, right, top)
        if segment_text.strip():
            segments.append(
                TextSegment(
                    text=segment_text,
                    bbox=BoundingBox(x0=left, y0=height - top, x1=right, y1=height - bottom),
                )
            )
    return PageText(
        page_number=page_number,
        page_width=width,
        page_height=height,
        text=textpage.get_text_range(),
        segments=segments,
    )


def _measure(page: pdfium.PdfPage, text: str, width: float, height: float) -> PageMeasurements:
    text_modes = [
        pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT])
    ]
    invisible = sum(1 for mode in text_modes if mode == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE)
    bounds = [obj.get_bounds() for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])]
    return PageMeasurements(
        char_count=sum(1 for ch in text if not ch.isspace()),
        unreliable_char_ratio=unreliable_char_ratio(text),
        invisible_text_ratio=invisible / len(text_modes) if text_modes else 0.0,
        image_coverage=image_coverage(bounds, width, height),
    )


def _render(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    bitmap = page.render(scale=dpi / 72)
    try:
        # convert() copies, so the image does not alias PDFium-owned memory.
        image: Image.Image = bitmap.to_pil().convert("RGB")
        return image
    finally:
        bitmap.close()


def check_pdf(pdf: pdfium.PdfDocument, limits: PdfLimits) -> int:
    """Validate document-level limits before any per-page work. Returns the page count."""
    page_count = len(pdf)
    if page_count == 0:
        raise AppError(ErrorCode.CORRUPT_DOCUMENT, "PDF contains no pages")
    if page_count > limits.max_pages:
        raise AppError(
            ErrorCode.TOO_MANY_PAGES,
            f"PDF has {page_count} pages; the limit is {limits.max_pages}",
        )
    return page_count


def process_pages(pdf: pdfium.PdfDocument, limits: PdfLimits) -> Iterator[ProcessedPage]:
    """Yield pages in document order. Must be consumed while ``pdf`` is open (inside the lock)."""
    for index in range(len(pdf)):
        page_number = index + 1
        page = pdf[index]
        textpage = None
        try:
            width, height = page.get_size()
            textpage = page.get_textpage()
            page_text = _extract_text(textpage, page_number, width, height)
            decision = classify_page(
                _measure(page, page_text.text, width, height), limits.native_text_min_chars
            )
            has_text = bool(page_text.text.strip())

            image = None
            image_info = None
            if decision.needs_ocr:
                px_w, px_h = _render_size(width, height, limits.render_dpi)
                if px_w * px_h > limits.max_page_pixels:
                    raise AppError(
                        ErrorCode.PAGE_TOO_LARGE,
                        f"Page {page_number} exceeds the pixel limit at {limits.render_dpi} DPI",
                    )
                image = _render(page, limits.render_dpi)
                image_info = PageImageInfo(
                    width_px=image.width, height_px=image.height, dpi=limits.render_dpi
                )

            manifest = PageManifest(
                page_number=page_number,
                source=PageSource.PDF_RENDER if decision.needs_ocr else PageSource.PDF_TEXT,
                needs_ocr=decision.needs_ocr,
                ocr_reason=decision.reason,
                width_pt=width,
                height_pt=height,
                rotation=page.get_rotation(),
                embedded_text_chars=len(page_text.text),
                has_embedded_text=has_text,
                image=image_info,
            )
            yield ProcessedPage(manifest, page_text if has_text else None, image)
        except pdfium.PdfiumError as exc:
            raise AppError(
                ErrorCode.CORRUPT_DOCUMENT, f"Page {page_number} could not be processed"
            ) from exc
        finally:
            if textpage is not None:
                textpage.close()
            page.close()
