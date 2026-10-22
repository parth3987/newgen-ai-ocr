"""Inspection overlay: regions, reading order, table cells and review flags on the page image.

For a human checking layout errors; generated on request from the stored layout and image,
never stored.
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout.models import PageLayout, RegionType, TableStatus

Image = npt.NDArray[np.uint8]

# BGR colours per region type (OpenCV convention); unlisted types are grey.
COLOURS: dict[RegionType, tuple[int, int, int]] = {
    RegionType.TITLE: (180, 0, 180),
    RegionType.SECTION_TITLE: (180, 0, 180),
    RegionType.TEXT: (200, 120, 0),
    RegionType.CAPTION: (0, 140, 200),
    RegionType.HEADER: (0, 160, 0),
    RegionType.FOOTER: (0, 160, 0),
    RegionType.PAGE_NUMBER: (0, 160, 0),
    RegionType.TABLE: (0, 0, 220),
    RegionType.UNCLASSIFIED: (0, 0, 255),
}
_GREY = (128, 128, 128)
_CELL = (0, 120, 255)
_REVIEW = (0, 0, 255)


def _rect(image: Image, b: BoundingBox, colour: tuple[int, int, int], width: int) -> None:
    cv2.rectangle(image, (int(b.x0), int(b.y0)), (int(b.x1), int(b.y1)), colour, width)


def render(image: Image, layout: PageLayout) -> bytes:
    """PNG bytes of the page with the layout drawn on it."""
    code = cv2.COLOR_GRAY2BGR if image.ndim == 2 else cv2.COLOR_RGB2BGR
    canvas = np.asarray(cv2.cvtColor(image, code), dtype=np.uint8)
    scale = max(canvas.shape[1] / 1600, 0.5)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for table in layout.tables:
        for cell in table.cells:
            if cell.bbox is not None:
                _rect(canvas, cell.bbox, _CELL, 1)
    for region in layout.regions:
        colour = COLOURS.get(region.type, _GREY)
        _rect(canvas, region.bbox, colour, max(2, int(2 * scale)))
        flagged = any(
            t.region_id == region.id and t.status is not TableStatus.OK for t in layout.tables
        )
        flag = " !" if flagged else ""
        label = f"{region.order} {region.type.value}{flag}"
        origin = (int(region.bbox.x0), max(int(region.bbox.y0) - 6, 12))
        cv2.putText(canvas, label, origin, font, 0.6 * scale, colour, max(1, int(scale)))
    if layout.review.needs_review:
        text = "REVIEW: " + ", ".join(r.value for r in layout.review.reasons)
        cv2.putText(canvas, text[:120], (10, int(30 * scale)), font, 0.7 * scale, _REVIEW, 2)
    ok, encoded = cv2.imencode(".png", canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return bytes(encoded.tobytes())
