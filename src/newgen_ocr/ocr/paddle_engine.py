"""PaddleOCR adapter (primary engine): local, hash-verified models only."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.ocr.engine import Image, reading_order
from newgen_ocr.ocr.model_store import ModelEntry, ModelStore
from newgen_ocr.ocr.models import EngineInfo, OcrLine
from newgen_ocr.ocr.paddle_runtime import load_paddleocr

# The PP-LCNet text-line orientation classifier is applied only where it was validated on
# the baseline corpus: English. On Indic scripts it misclassifies upright lines as upside
# down (measured: Hindi CER 0.897 with it vs 0.007 without; 9 of 15 lines flipped on a
# Tamil serif scan, CER 0.707), garbling text and page order (ADR-0021).
TEXTLINE_ORIENTATION_LANGUAGES = frozenset({"en"})


class PaddleEngine:
    """One PaddleOCR pipeline per recognition model, created lazily and reused.

    Paddle predictors are not thread-safe; calls are serialised per engine instance.
    Paddle recognises one script per call: the first requested language selects the model.
    """

    name = "paddle"

    def __init__(
        self,
        store: ModelStore,
        cache_dir: Path,
        detection_model: str,
        cpu_threads: int | None,
        enable_mkldnn: bool = True,
    ) -> None:
        self.store = store
        self.cache_dir = cache_dir
        self.detection_model = detection_model
        self.cpu_threads = cpu_threads
        self.enable_mkldnn = enable_mkldnn
        self._pipelines: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ model selection

    def _detection(self) -> ModelEntry:
        found = self.store.find("paddleocr", "detection", name=self.detection_model)
        if not found:
            raise AppError(
                ErrorCode.MODEL_NOT_AVAILABLE,
                f"Detection model {self.detection_model} is not in models.lock.json",
            )
        return found[0]

    def _orientation(self, languages: Sequence[str] = ()) -> ModelEntry | None:
        if languages and languages[0] not in TEXTLINE_ORIENTATION_LANGUAGES:
            return None
        found = self.store.find("paddleocr", "textline_orientation")
        return found[0] if found else None

    def _recognition(self, languages: Sequence[str]) -> ModelEntry:
        if not languages:
            raise AppError(ErrorCode.UNSUPPORTED_LANGUAGE, "At least one language is required")
        entry = self.store.for_language("paddleocr", languages[0])
        if entry is None:
            raise AppError(
                ErrorCode.UNSUPPORTED_LANGUAGE, f"No PaddleOCR model for language {languages[0]}"
            )
        return entry

    def _models(self, languages: Sequence[str]) -> list[ModelEntry]:
        models = [self._detection(), self._recognition(languages)]
        orientation = self._orientation(languages)
        if orientation is not None:
            models.append(orientation)
        return models

    # ------------------------------------------------------------ protocol

    def unavailable_reason(self) -> str | None:
        try:
            metadata.version("paddleocr")
        except metadata.PackageNotFoundError:
            return "paddleocr is not installed"
        if not self.store.is_present(self._detection()):
            return f"detection model {self.detection_model} is not present"
        if not self.languages():
            return "no recognition models are present"
        return None

    def languages(self) -> frozenset[str]:
        present: set[str] = set()
        for entry in self.store.find("paddleocr", "recognition"):
            if self.store.is_present(entry):
                present.update(entry.languages)
        return frozenset(present)

    def info(self, languages: Sequence[str]) -> EngineInfo:
        return EngineInfo(
            name=self.name,
            version=(
                f"paddleocr {metadata.version('paddleocr')}; "
                f"paddlepaddle {metadata.version('paddlepaddle')}"
            ),
            models=[m.ref() for m in self._models(languages)],
            parameters={
                "detection_model": self.detection_model,
                "recognition_language": languages[0] if languages else "",
                "textline_orientation": self._orientation(languages) is not None,
                "cpu_threads": self.cpu_threads or 0,
                "enable_mkldnn": self.enable_mkldnn,
            },
        )

    def _pipeline(self, languages: Sequence[str]) -> Any:
        detection, recognition = self._detection(), self._recognition(languages)
        orientation = self._orientation(languages)
        key = f"{recognition.id}|{orientation is not None}"
        if key not in self._pipelines:
            paddleocr = load_paddleocr(self.cache_dir)
            kwargs: dict[str, Any] = {
                "text_detection_model_name": detection.name,
                "text_detection_model_dir": str(self.store.verify(detection)),
                "text_recognition_model_name": recognition.name,
                "text_recognition_model_dir": str(self.store.verify(recognition)),
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": orientation is not None,
                "device": "cpu",
                # oneDNN is ~5x faster on CPU (docs/PERFORMANCE.md). Requires paddlepaddle 3.2.x;
                # 3.3.1 fails with oneDNN on PP-OCRv5 detection (ADR-0020).
                "enable_mkldnn": self.enable_mkldnn,
            }
            if orientation is not None:
                kwargs["textline_orientation_model_name"] = orientation.name
                kwargs["textline_orientation_model_dir"] = str(self.store.verify(orientation))
            if self.cpu_threads is not None:
                kwargs["cpu_threads"] = self.cpu_threads
            self._pipelines[key] = paddleocr.PaddleOCR(**kwargs)
        return self._pipelines[key]

    def recognize(self, image: Image, languages: Sequence[str]) -> list[OcrLine]:
        # PaddleX expects 3-channel BGR arrays, the OpenCV convention.
        if image.ndim == 2:
            bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        with self._lock:
            pipeline = self._pipeline(languages)
            try:
                results = pipeline.predict(np.ascontiguousarray(bgr))
            except AppError:
                raise
            except Exception as exc:
                raise AppError(ErrorCode.OCR_FAILED, "PaddleOCR failed on this page") from exc
        return reading_order(_to_lines(results[0] if results else None))


def _to_lines(result: Any) -> list[OcrLine]:
    if result is None:
        return []
    texts = result["rec_texts"]
    scores = result["rec_scores"]
    polys = result["rec_polys"]
    # 0 upright, 1 upside down, -1 not classified (orientation model disabled).
    angles = list(result.get("textline_orientation_angles", [])) or [-1] * len(texts)
    lines = []
    for text, score, poly, angle in zip(texts, scores, polys, angles, strict=True):
        if not str(text).strip():
            continue
        points = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
        lines.append(
            OcrLine(
                text=str(text),
                confidence=round(float(score), 4),
                bbox=BoundingBox(
                    x0=float(points[:, 0].min()),
                    y0=float(points[:, 1].min()),
                    x1=float(points[:, 0].max()),
                    y1=float(points[:, 1].max()),
                ),
                polygon=[(float(x), float(y)) for x, y in points],
                orientation_deg={0: 0, 1: 180}.get(int(angle)),
            )
        )
    return lines
