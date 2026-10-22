"""The common OCR engine interface. The pipeline depends only on this protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt

from newgen_ocr.ocr.models import EngineInfo, OcrLine

Image = npt.NDArray[np.uint8]  # greyscale (H, W) or RGB (H, W, 3)


class OcrEngine(Protocol):
    @property
    def name(self) -> str: ...

    def unavailable_reason(self) -> str | None:
        """None if the engine can run; otherwise a short operator-facing reason."""
        ...

    def languages(self) -> frozenset[str]:
        """ISO 639-1 codes this engine has verified local models for."""
        ...

    def info(self, languages: Sequence[str]) -> EngineInfo:
        """Engine version and the exact models used for these languages."""
        ...

    def recognize(self, image: Image, languages: Sequence[str]) -> list[OcrLine]:
        """Recognise text. Raises AppError (OCR_FAILED, UNSUPPORTED_LANGUAGE, ...) on failure.

        Returns lines in reading order (see reading_order), in the input image's coordinates.
        """
        ...


def page_orientation(lines: list[OcrLine]) -> int | None:
    """180 if most orientation-classified lines are upside down, 0 if most are upright."""
    known = [line.orientation_deg for line in lines if line.orientation_deg is not None]
    if not known:
        return None
    return 180 if sum(1 for o in known if o == 180) > len(known) / 2 else 0


def reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Sort into reading order, grouping lines whose vertical centres are within half a line.

    Coordinates stay in the image as read. For an upside-down page (see page_orientation)
    the upright reading order is bottom-to-top, right-to-left in image coordinates.
    """
    if not lines:
        return lines
    heights = sorted(line.bbox.y1 - line.bbox.y0 for line in lines)
    tolerance = max(heights[len(heights) // 2] / 2, 1.0)
    flipped = page_orientation(lines) == 180

    def key(line: OcrLine) -> tuple[int, float]:
        centre = (line.bbox.y0 + line.bbox.y1) / 2
        row, x = int(centre // tolerance), line.bbox.x0
        return (-row, -line.bbox.x1) if flipped else (row, x)

    return sorted(lines, key=key)
