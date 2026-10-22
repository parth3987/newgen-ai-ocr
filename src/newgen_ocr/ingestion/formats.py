"""Approved input formats, detected by content signature (magic bytes).

The client-declared filename and Content-Type are never trusted to determine the
format. A filename extension that contradicts the detected format is rejected.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath

from newgen_ocr.errors import AppError, ErrorCode


class DocumentFormat(StrEnum):
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    TIFF = "tiff"


# Signatures must match at offset 0. BigTIFF ("II+", "MM\0+") is deliberately excluded.
_SIGNATURES: tuple[tuple[bytes, DocumentFormat], ...] = (
    (b"%PDF-", DocumentFormat.PDF),
    (b"\xff\xd8\xff", DocumentFormat.JPEG),
    (b"\x89PNG\r\n\x1a\n", DocumentFormat.PNG),
    (b"II*\x00", DocumentFormat.TIFF),
    (b"MM\x00*", DocumentFormat.TIFF),
)
SIGNATURE_BYTES = max(len(sig) for sig, _ in _SIGNATURES)

EXTENSIONS: dict[DocumentFormat, frozenset[str]] = {
    DocumentFormat.PDF: frozenset({".pdf"}),
    DocumentFormat.JPEG: frozenset({".jpg", ".jpeg"}),
    DocumentFormat.PNG: frozenset({".png"}),
    DocumentFormat.TIFF: frozenset({".tif", ".tiff"}),
}

MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.JPEG: "image/jpeg",
    DocumentFormat.PNG: "image/png",
    DocumentFormat.TIFF: "image/tiff",
}


def sniff_format(head: bytes) -> DocumentFormat | None:
    for signature, fmt in _SIGNATURES:
        if head.startswith(signature):
            return fmt
    return None


def detect_format(head: bytes, allowed: frozenset[DocumentFormat]) -> DocumentFormat:
    fmt = sniff_format(head)
    if fmt is None or fmt not in allowed:
        raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "File type is not an approved format")
    return fmt


def safe_filename(filename: str | None) -> str | None:
    """Basename only, control characters removed, bounded length. For metadata, never paths."""
    if not filename:
        return None
    name = PureWindowsPath(PurePosixPath(filename).name).name
    name = "".join(ch for ch in name if ch.isprintable())[:255].strip()
    return name or None


def check_extension(fmt: DocumentFormat, filename: str | None) -> None:
    """Reject a filename extension that contradicts the detected format."""
    name = safe_filename(filename)
    if name is None:
        return
    suffix = PurePosixPath(name).suffix.lower()
    if suffix and suffix not in EXTENSIONS[fmt]:
        raise AppError(
            ErrorCode.FORMAT_MISMATCH,
            f"File extension does not match detected content type ({fmt.value})",
        )
