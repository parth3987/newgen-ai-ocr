"""PaddleOCR adapter against the real, locally fetched models (marked `models`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.evaluation.corpus import DEFAULT_FONT_DIR, LANGUAGE_FONTS, load_lines, render_page
from newgen_ocr.evaluation.metrics import cer
from newgen_ocr.ocr.engine import page_orientation
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.paddle_engine import PaddleEngine
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT
from tests.fixtures.pages import blank_page

pytestmark = pytest.mark.models

TEXT_DIR = ROOT / "evaluation" / "corpus" / "text"


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PaddleEngine]:
    store = ModelStore(MODELS_DIR, MODELS_LOCK)
    yield PaddleEngine(store, tmp_path_factory.mktemp("pdx"), "PP-OCRv5_mobile_det", None)


def _page(language: str, lines: int = 6, dpi: int = 200) -> tuple[np.ndarray, str]:
    text = load_lines(TEXT_DIR, language)[:lines]
    font = DEFAULT_FONT_DIR / LANGUAGE_FONTS[language][0]
    page = render_page(text, font, dpi)
    return page[: dpi * 3], "\n".join(text)  # top 3 inches: keeps tests fast


def _text(engine: PaddleEngine, image: np.ndarray, language: str) -> str:
    return "\n".join(line.text for line in engine.recognize(image, [language]))


def test_english_accuracy_and_schema(engine: PaddleEngine) -> None:
    image, truth = _page("en")
    lines = engine.recognize(image, ["en"])
    assert cer(truth, "\n".join(line.text for line in lines)) < 0.02
    for line in lines:
        assert line.confidence is not None and 0.0 <= line.confidence <= 1.0
        assert line.polygon is not None and len(line.polygon) == 4
        assert 0 <= line.bbox.x0 < line.bbox.x1 <= image.shape[1]
        assert 0 <= line.bbox.y0 < line.bbox.y1 <= image.shape[0]
    assert [line.bbox.y0 for line in lines] == sorted(line.bbox.y0 for line in lines)


@pytest.mark.parametrize("language", ["hi", "mr", "te", "ta"])
def test_indic_language_models(engine: PaddleEngine, language: str) -> None:
    image, truth = _page(language, lines=3)
    assert cer(truth, _text(engine, image, language)) < 0.1


def test_upside_down_page_is_detected_and_ordered(engine: PaddleEngine) -> None:
    image, truth = _page("en")
    flipped = np.ascontiguousarray(cv2.rotate(image, cv2.ROTATE_180))
    lines = engine.recognize(flipped, ["en"])
    assert page_orientation(lines) == 180
    assert cer(truth, "\n".join(line.text for line in lines)) < 0.05


@pytest.mark.parametrize("language", ["hi", "mr", "te", "ta", "kn"])
def test_orientation_classifier_disabled_for_indic_scripts(
    engine: PaddleEngine, language: str
) -> None:
    """The classifier flips upright Indic lines (ADR-0021); it must stay off for them."""
    assert len(engine.info([language]).models) == 2
    image, _ = _page(language, lines=2)
    lines = engine.recognize(image, [language])
    assert all(line.orientation_deg is None for line in lines)
    assert page_orientation(lines) is None


def test_blank_page_returns_no_lines(engine: PaddleEngine) -> None:
    assert engine.recognize(blank_page(dpi=100), ["en"]) == []


def test_rgb_and_greyscale_inputs_agree(engine: PaddleEngine) -> None:
    image, _ = _page("en", lines=2)
    rgb = np.ascontiguousarray(np.stack([image] * 3, axis=-1))
    assert _text(engine, image, "en") == _text(engine, rgb, "en")


def test_info_records_exact_models(engine: PaddleEngine) -> None:
    info = engine.info(["en"])
    assert info.name == "paddle"
    assert "paddleocr 3.7.0" in info.version and "paddlepaddle 3.2.2" in info.version
    lock = {m["id"]: m for m in json.loads(MODELS_LOCK.read_text())["models"]}
    ids = [m.id for m in info.models]
    assert ids == [
        "paddle/PP-OCRv5_mobile_det",
        "paddle/en_PP-OCRv5_mobile_rec",
        "paddle/PP-LCNet_x1_0_textline_ori",
    ]
    assert [m.id for m in engine.info(["te"]).models][1] == "paddle/te_PP-OCRv5_mobile_rec"
    for model in info.models:
        assert model.revision == lock[model.id]["source"]["revision"]
        assert model.files == lock[model.id]["files"]


def test_kannada_maps_to_v3_model(engine: PaddleEngine) -> None:
    assert [m.id for m in engine.info(["kn"]).models][1] == "paddle/ka_PP-OCRv3_mobile_rec"


def test_unsupported_language(engine: PaddleEngine) -> None:
    with pytest.raises(AppError) as exc:
        engine.recognize(blank_page(dpi=50), ["fr"])
    assert exc.value.code is ErrorCode.UNSUPPORTED_LANGUAGE


def test_missing_models_report_unavailable(tmp_path: Path) -> None:
    engine = PaddleEngine(
        ModelStore(tmp_path, MODELS_LOCK), tmp_path / "c", "PP-OCRv5_mobile_det", None
    )
    assert "not present" in (engine.unavailable_reason() or "")
    with pytest.raises(AppError) as exc:
        engine.recognize(blank_page(dpi=50), ["en"])
    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE
