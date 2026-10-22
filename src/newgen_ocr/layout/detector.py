"""Layout and table models (PP-DocLayout, PP-LCNet table classifier, SLANet_plus; ADR-0025).

Local, hash-verified models only, loaded through the offline PaddleOCR lockdown. The service
depends on the ``LayoutModels`` protocol, so tests and future models can replace this.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import RegionType, TableKind
from newgen_ocr.ocr.model_store import ModelEntry, ModelStore
from newgen_ocr.ocr.models import EngineInfo
from newgen_ocr.ocr.paddle_runtime import load_paddleocr, run_on_paddle_thread

logger = logging.getLogger(__name__)

Image = npt.NDArray[np.uint8]

LAYOUT_MODELS = ("PP-DocLayout_plus-L", "PP-DocLayout-M")

# Model label -> stable region type. Covers both pinned layout models' label lists.
LABELS: dict[str, RegionType] = {
    "doc_title": RegionType.TITLE,
    "paragraph_title": RegionType.SECTION_TITLE,
    "text": RegionType.TEXT,
    "abstract": RegionType.TEXT,
    "content": RegionType.TEXT,  # table of contents
    "reference": RegionType.TEXT,
    "reference_content": RegionType.TEXT,
    "algorithm": RegionType.TEXT,
    "figure_title": RegionType.CAPTION,
    "table_title": RegionType.CAPTION,
    "chart_title": RegionType.CAPTION,
    "header": RegionType.HEADER,
    "footer": RegionType.FOOTER,
    "number": RegionType.PAGE_NUMBER,
    "footnote": RegionType.FOOTNOTE,
    "aside_text": RegionType.ASIDE,
    "table": RegionType.TABLE,
    "image": RegionType.FIGURE,
    "header_image": RegionType.FIGURE,
    "footer_image": RegionType.FIGURE,
    "chart": RegionType.CHART,
    "formula": RegionType.FORMULA,
    "formula_number": RegionType.FORMULA,
    "seal": RegionType.SEAL,
}


@dataclass(frozen=True)
class DetectedRegion:
    label: str
    type: RegionType
    score: float
    bbox: BoundingBox


@dataclass(frozen=True)
class TableStructure:
    tokens: list[str]
    cell_boxes: list[BoundingBox]  # crop pixels, one per cell in token order
    score: float


class LayoutModels(Protocol):
    def unavailable_reason(self) -> str | None: ...

    def info(self) -> list[EngineInfo]: ...

    def detect(self, image: Image) -> list[DetectedRegion]: ...

    def classify_table(self, crop: Image) -> tuple[TableKind, float]: ...

    def table_structure(self, crop: Image) -> TableStructure: ...


def _bgr(image: Image) -> Image:
    code = cv2.COLOR_GRAY2BGR if image.ndim == 2 else cv2.COLOR_RGB2BGR
    return np.ascontiguousarray(cv2.cvtColor(image, code), dtype=np.uint8)


class PaddleLayoutModels:
    """Lazily created predictors; calls are serialised and run on the Paddle thread."""

    def __init__(
        self,
        store: ModelStore,
        cache_dir: Path,
        layout_model: str,
        score_threshold: float,
        cpu_threads: int | None,
        enable_mkldnn: bool = True,
    ) -> None:
        if layout_model not in LAYOUT_MODELS:
            raise ValueError(f"unknown layout model {layout_model!r}")
        self.store = store
        self.cache_dir = cache_dir
        self.layout_model = layout_model
        self.score_threshold = score_threshold
        self.cpu_threads = cpu_threads
        self.enable_mkldnn = enable_mkldnn
        self._predictors: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _entry(self, kind: str, name: str | None = None) -> ModelEntry:
        found = self.store.find("paddleocr", kind, name=name)
        if not found:
            raise AppError(ErrorCode.MODEL_NOT_AVAILABLE, f"No {kind} model in models.lock.json")
        return found[0]

    def _entries(self) -> list[ModelEntry]:
        return [
            self._entry("layout_detection", self.layout_model),
            self._entry("table_classification"),
            self._entry("table_structure"),
        ]

    def unavailable_reason(self) -> str | None:
        try:
            metadata.version("paddleocr")
        except metadata.PackageNotFoundError:
            return "paddleocr is not installed"
        try:
            entries = self._entries()
        except AppError as exc:
            return exc.message
        missing = [e.name for e in entries if not self.store.is_present(e)]
        return f"models not present: {', '.join(missing)}" if missing else None

    def info(self) -> list[EngineInfo]:
        version = (
            f"paddleocr {metadata.version('paddleocr')}; "
            f"paddlepaddle {metadata.version('paddlepaddle')}"
        )
        return [
            EngineInfo(
                name=entry.kind,
                version=version,
                models=[entry.ref()],
                parameters={"score_threshold": self.score_threshold}
                if entry.kind == "layout_detection"
                else {},
            )
            for entry in self._entries()
        ]

    def _predictor(self, kind: str) -> Any:
        if kind in self._predictors:
            return self._predictors[kind]
        name = self.layout_model if kind == "layout_detection" else None
        entry = self._entry(kind, name)
        paddleocr = load_paddleocr(self.cache_dir)
        kwargs: dict[str, Any] = {
            "model_name": entry.name,
            "model_dir": str(self.store.verify(entry)),
            "device": "cpu",
            "enable_mkldnn": self.enable_mkldnn,
        }
        if self.cpu_threads is not None:
            kwargs["cpu_threads"] = self.cpu_threads
        if kind == "layout_detection":
            factory = paddleocr.LayoutDetection
            kwargs.update(threshold=self.score_threshold, layout_nms=True)
        elif kind == "table_classification":
            factory = paddleocr.TableClassification
        else:
            factory = paddleocr.TableStructureRecognition
        self._predictors[kind] = factory(**kwargs)
        return self._predictors[kind]

    def _predict(self, kind: str, image: Image) -> Any:
        with self._lock:
            results = run_on_paddle_thread(self._run, kind, _bgr(image))
        if not results:
            raise AppError(ErrorCode.LAYOUT_FAILED, f"{kind} returned no result")
        return results[0]

    def _run(self, kind: str, bgr: Image) -> Any:
        predictor = self._predictor(kind)
        try:
            return predictor.predict(bgr)
        except AppError:
            raise
        except Exception as exc:
            logger.warning(
                "layout model failed", extra={"model": kind, "error_type": type(exc).__name__}
            )
            raise AppError(ErrorCode.LAYOUT_FAILED, f"{kind} failed on this page") from exc

    def detect(self, image: Image) -> list[DetectedRegion]:
        result = self._predict("layout_detection", image)
        regions = []
        for item in result["boxes"]:
            label = str(item["label"])
            x0, y0, x1, y1 = (float(v) for v in item["coordinate"])
            regions.append(
                DetectedRegion(
                    label=label,
                    type=LABELS.get(label, RegionType.UNCLASSIFIED),
                    score=round(float(item["score"]), 4),
                    bbox=g.box(x0, y0, x1, y1),
                )
            )
        return regions

    def classify_table(self, crop: Image) -> tuple[TableKind, float]:
        result = self._predict("table_classification", crop)
        label = str(result["label_names"][0])
        kind = TableKind.WIRED if label == "wired_table" else TableKind.WIRELESS
        return kind, round(float(result["scores"][0]), 4)

    def table_structure(self, crop: Image) -> TableStructure:
        result = self._predict("table_structure", crop)
        boxes = [g.from_points([float(v) for v in points]) for points in result["bbox"]]
        return TableStructure(
            tokens=[str(t) for t in result["structure"]],
            cell_boxes=boxes,
            score=round(float(result["structure_score"]), 4),
        )
