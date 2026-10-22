"""Layout and table models against the real, locally fetched models (marked `models`)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from newgen_ocr.evaluation.layout_corpus import columns_page, table_page, table_specs
from newgen_ocr.layout.detector import PaddleLayoutModels
from newgen_ocr.layout.models import RegionType, RulingCheck, TableKind
from newgen_ocr.layout.table_structure import compare_with_rulings, parse_structure, trace_rulings
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.paddle_engine import PaddleEngine
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT

pytestmark = pytest.mark.models

TEXT_DIR = ROOT / "evaluation" / "corpus" / "text"
SPECS = {s.id: s for s in table_specs(TEXT_DIR)}


@pytest.fixture(scope="module")
def cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pdx")


@pytest.fixture(scope="module")
def models(cache: Path) -> Iterator[PaddleLayoutModels]:
    store = ModelStore(MODELS_DIR, MODELS_LOCK)
    yield PaddleLayoutModels(store, cache, "PP-DocLayout_plus-L", 0.5, None)


def _table_crop(models: PaddleLayoutModels, image: np.ndarray) -> np.ndarray:
    tables = [r for r in models.detect(image) if r.type is RegionType.TABLE]
    assert len(tables) == 1
    b = tables[0].bbox
    return np.ascontiguousarray(image[int(b.y0) - 5 : int(b.y1) + 5, int(b.x0) - 5 : int(b.x1) + 5])


@pytest.mark.parametrize(
    "spec_id", ["en-colspan", "en-rowspan", "en-two-level-header", "te-simple"]
)
def test_wired_table_structure_is_exact_and_verified(
    models: PaddleLayoutModels, spec_id: str
) -> None:
    spec = SPECS[spec_id]
    image, truth = table_page(spec, TEXT_DIR, 200)
    crop = _table_crop(models, image)
    assert models.classify_table(crop)[0] is TableKind.WIRED
    structure = models.table_structure(crop)
    cells, rows, cols = parse_structure(structure.tokens)
    expected = {(c.row, c.col, c.row_span, c.col_span) for c in truth.tables[0].cells}
    assert {(c.row, c.col, c.row_span, c.col_span) for c in cells} == expected
    assert len(structure.cell_boxes) == len(cells)
    traced = trace_rulings(crop)
    assert compare_with_rulings(cells, rows, cols, traced) is RulingCheck.VERIFIED


def test_page_furniture_and_columns_are_detected(models: PaddleLayoutModels) -> None:
    image, _ = columns_page(TEXT_DIR, 200)
    regions = models.detect(image)
    types = [r.type for r in regions]
    assert types.count(RegionType.HEADER) == 2
    assert RegionType.PAGE_NUMBER in types or RegionType.FOOTER in types
    assert types.count(RegionType.TEXT) >= 2


def test_paddle_calls_from_many_threads(models: PaddleLayoutModels, cache: Path) -> None:
    """Regression: with oneDNN, Paddle predictors used from several OS threads failed
    (RuntimeError: std::exception) and could return degraded OCR; every call is now
    confined to one Paddle thread (paddle_runtime.run_on_paddle_thread)."""
    engine = PaddleEngine(ModelStore(MODELS_DIR, MODELS_LOCK), cache, "PP-OCRv5_mobile_det", None)
    pages = [table_page(SPECS[i], TEXT_DIR, 200)[0] for i in ("en-simple", "en-colspan")]
    expected = [len(engine.recognize(p, ["en"])) for p in pages]
    results: list[tuple[int, int, int]] = []
    errors: list[BaseException] = []

    def job(index: int) -> None:
        try:
            page = pages[index % 2]
            lines = engine.recognize(page, ["en"])
            regions = models.detect(page)
            results.append((index % 2, len(lines), len(regions)))
        except BaseException as exc:  # noqa: BLE001  (collected for the assertion)
            errors.append(exc)

    for index in range(6):
        thread = threading.Thread(target=job, args=(index,))
        thread.start()
        thread.join()
    assert errors == []
    assert all(lines == expected[i] for i, lines, _ in results)
