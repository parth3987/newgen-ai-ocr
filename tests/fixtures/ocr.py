"""Test doubles for OCR engines."""

from __future__ import annotations

from collections.abc import Sequence

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.ocr.engine import Image
from newgen_ocr.ocr.models import EngineInfo, OcrLine


class FakeEngine:
    """Returns fixed lines (or raises) and records every call."""

    def __init__(
        self,
        name: str,
        text: str = "hello world",
        *,
        fail_with: ErrorCode | None = None,
        unavailable: str | None = None,
        languages: Sequence[str] = ("en", "hi", "mr", "te", "ta", "kn"),
    ) -> None:
        self._name = name
        self.text = text
        self.fail_with = fail_with
        self.unavailable = unavailable
        self._languages = frozenset(languages)
        self.calls: list[tuple[tuple[int, ...], tuple[str, ...]]] = []

    @property
    def name(self) -> str:
        return self._name

    def unavailable_reason(self) -> str | None:
        return self.unavailable

    def languages(self) -> frozenset[str]:
        return self._languages

    def info(self, languages: Sequence[str]) -> EngineInfo:
        return EngineInfo(name=self._name, version="fake 1.0", models=[], parameters={})

    def recognize(self, image: Image, languages: Sequence[str]) -> list[OcrLine]:
        self.calls.append((tuple(image.shape), tuple(languages)))
        if self.fail_with is not None:
            raise AppError(self.fail_with, "simulated failure")
        return [
            OcrLine(
                text=line,
                confidence=0.9,
                bbox=BoundingBox(x0=10, y0=10 + 30 * i, x1=200, y1=35 + 30 * i),
            )
            for i, line in enumerate(self.text.splitlines())
            if line
        ]
