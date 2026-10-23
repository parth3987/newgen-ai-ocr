"""Source crops and page previews for reviewers (ADR-0028).

Images are cut from the exact page image the OCR engine read, so evidence boxes line up.
When Aadhaar masking is on, every Aadhaar number the OCR read on the page is blacked out
(first eight digits; position estimated from the character offsets within the OCR line,
with a margin). This is best effort: digits the OCR did not read cannot be found (OD-30).
"""

from __future__ import annotations

import io
from collections.abc import Iterable

import numpy as np
from PIL import Image, ImageDraw

from newgen_ocr.extraction.identifiers import AADHAAR_RE
from newgen_ocr.extraction.normalize import ascii_digits
from newgen_ocr.ingestion.models import BoundingBox

HIGHLIGHT = (220, 30, 30)
CANDIDATE = (30, 90, 220)
MAX_WIDTH = 1400


def aadhaar_redactions(lines: Iterable[tuple[str, BoundingBox]]) -> list[BoundingBox]:
    """Boxes over the first eight digits of every Aadhaar number in these text lines."""
    boxes = []
    for raw, bbox in lines:
        text = ascii_digits(raw)
        width = bbox.x1 - bbox.x0
        for match in AADHAAR_RE.finditer(text):
            digits = [i for i in range(match.start(1), match.end(1)) if text[i].isdigit()]
            if len(digits) < 12:
                continue
            start, end = digits[0], digits[7] + 1  # the first eight digits
            char = width / max(len(text), 1)
            boxes.append(
                BoundingBox(
                    x0=max(bbox.x0, bbox.x0 + start * char - char),
                    y0=bbox.y0 - 2,
                    x1=min(bbox.x1, bbox.x0 + end * char + char),
                    y1=bbox.y1 + 2,
                )
            )
    return boxes


def _rgb(image: np.ndarray) -> Image.Image:
    return Image.fromarray(image).convert("RGB")


def _scaled(box: BoundingBox, scale: float) -> tuple[float, float, float, float]:
    return box.x0 * scale, box.y0 * scale, box.x1 * scale, box.y1 * scale


def _png(img: Image.Image) -> bytes:
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)))
    buffer = io.BytesIO()
    img.save(buffer, "PNG", compress_level=3)
    return buffer.getvalue()


def _draw(
    img: Image.Image,
    boxes: list[BoundingBox],
    candidates: list[BoundingBox],
    redact: list[BoundingBox],
    scale: float,
) -> None:
    draw = ImageDraw.Draw(img)
    for box in redact:
        draw.rectangle(_scaled(box, scale), fill=(0, 0, 0))
    width = max(2, round(img.width / 600))
    for box in candidates:
        draw.rectangle(_scaled(box, scale), outline=CANDIDATE, width=width)
    for box in boxes:
        draw.rectangle(_scaled(box, scale), outline=HIGHLIGHT, width=width)


def crop(
    image: np.ndarray,
    boxes: list[BoundingBox],
    redact: list[BoundingBox],
    scale: float = 1.0,
    context: float = 1.5,
) -> bytes:
    """The area around ``boxes`` (in page units; ``scale`` converts to pixels)."""
    img = _rgb(image)
    _draw(img, boxes, [], redact, scale)
    x0 = min(b.x0 for b in boxes) * scale
    y0 = min(b.y0 for b in boxes) * scale
    x1 = max(b.x1 for b in boxes) * scale
    y1 = max(b.y1 for b in boxes) * scale
    pad = max(20.0, (y1 - y0) * context)
    area = (
        max(0, round(x0 - pad * 2)),
        max(0, round(y0 - pad)),
        min(img.width, round(x1 + pad * 2)),
        min(img.height, round(y1 + pad)),
    )
    return _png(img.crop(area))


def page_preview(
    image: np.ndarray,
    boxes: list[BoundingBox],
    candidates: list[BoundingBox],
    redact: list[BoundingBox],
    scale: float = 1.0,
) -> bytes:
    img = _rgb(image)
    _draw(img, boxes, candidates, redact, scale)
    return _png(img)
