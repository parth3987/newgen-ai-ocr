from __future__ import annotations

import pytest

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import (
    DocumentFormat,
    check_extension,
    detect_format,
    safe_filename,
    sniff_format,
)
from tests.fixtures.builders import PageSpec, build_pdf, image_bytes

ALL = frozenset(DocumentFormat)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (build_pdf([PageSpec("x")]), DocumentFormat.PDF),
        (image_bytes("JPEG"), DocumentFormat.JPEG),
        (image_bytes("PNG"), DocumentFormat.PNG),
        (image_bytes("TIFF"), DocumentFormat.TIFF),
        (b"MM\x00*rest", DocumentFormat.TIFF),
    ],
)
def test_sniffs_approved_formats(data: bytes, expected: DocumentFormat) -> None:
    assert sniff_format(data[:16]) is expected


@pytest.mark.parametrize(
    "data",
    [
        image_bytes("GIF"),
        image_bytes("BMP"),
        b"PK\x03\x04 zip/docx",
        b"MZ\x90\x00 windows exe",
        b"II+\x00 bigtiff",
        b"  %PDF-1.4 leading junk",
        b"%PD",
        b"",
    ],
)
def test_rejects_unapproved_or_unknown(data: bytes) -> None:
    with pytest.raises(AppError) as exc:
        detect_format(data, ALL)
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


def test_respects_allowed_formats() -> None:
    with pytest.raises(AppError) as exc:
        detect_format(image_bytes("TIFF"), frozenset({DocumentFormat.PDF}))
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    ("fmt", "filename"),
    [
        (DocumentFormat.PDF, "scan.pdf"),
        (DocumentFormat.PDF, "SCAN.PDF"),
        (DocumentFormat.JPEG, "photo.jpg"),
        (DocumentFormat.JPEG, "photo.jpeg"),
        (DocumentFormat.TIFF, "fax.tif"),
        (DocumentFormat.PNG, "no_extension"),
        (DocumentFormat.PNG, None),
    ],
)
def test_extension_consistent(fmt: DocumentFormat, filename: str | None) -> None:
    check_extension(fmt, filename)


@pytest.mark.parametrize(
    ("fmt", "filename"),
    [
        (DocumentFormat.PDF, "invoice.png"),
        (DocumentFormat.JPEG, "report.pdf"),
        (DocumentFormat.PDF, "payload.exe"),
        (DocumentFormat.PNG, "image.png.html"),
    ],
)
def test_extension_mismatch_rejected(fmt: DocumentFormat, filename: str) -> None:
    with pytest.raises(AppError) as exc:
        check_extension(fmt, filename)
    assert exc.value.code is ErrorCode.FORMAT_MISMATCH


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\doc.pdf", "doc.pdf"),
        ("a\x00b\nc.pdf", "abc.pdf"),
        ("", None),
        (None, None),
        ("x" * 400 + ".pdf", "x" * 255),
    ],
)
def test_safe_filename(raw: str | None, expected: str | None) -> None:
    assert safe_filename(raw) == expected
