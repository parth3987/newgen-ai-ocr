from __future__ import annotations

import pytest

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.evaluation.metrics import cer
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.tesseract_engine import TesseractEngine, clean_text, parse_tsv
from tests.conftest import MODELS_DIR, MODELS_LOCK
from tests.fixtures.pages import blank_page, text_page

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)
TSV = "\n".join(
    [
        HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t800\t600\t-1\t",
        "4\t1\t1\t1\t1\t0\t10\t20\t300\t30\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t20\t100\t30\t96.5\tTotal",
        "5\t1\t1\t1\t1\t2\t120\t22\t90\t28\t88.0\tamount",
        "5\t1\t1\t1\t2\t1\t10\t70\t60\t30\t-1\t ",
        "5\t1\t1\t1\t2\t2\t80\t70\t120\t30\t42.0\tdue",
        "5\t1\t2\t1\t1\t1\t10\t200\t80\t30\t91.0\tपता",
    ]
)


def test_parse_tsv_groups_words_into_lines() -> None:
    lines = parse_tsv(TSV)
    assert [line.text for line in lines] == ["Total amount", "due", "पता"]
    first = lines[0]
    assert first.confidence == pytest.approx((0.965 + 0.88) / 2)
    assert (first.bbox.x0, first.bbox.y0, first.bbox.x1, first.bbox.y1) == (10, 20, 210, 50)
    assert first.words is not None and [w.text for w in first.words] == ["Total", "amount"]


def test_parse_tsv_empty_page() -> None:
    assert parse_tsv(HEADER + "\n1\t1\t0\t0\t0\t0\t0\t0\t800\t600\t-1\t\n") == []
    assert parse_tsv("") == []


def test_missing_binary_is_reported(tmp_path: object) -> None:
    store = ModelStore(MODELS_DIR, MODELS_LOCK)
    engine = TesseractEngine(store, command="definitely-not-tesseract", timeout_s=10, threads=1)
    assert "not found" in (engine.unavailable_reason() or "")
    with pytest.raises(AppError) as exc:
        engine.recognize(text_page(dpi=100), ["en"])
    assert exc.value.code is ErrorCode.OCR_ENGINE_UNAVAILABLE


def test_unsupported_language() -> None:
    engine = TesseractEngine(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 10, 1)
    with pytest.raises(AppError) as exc:
        engine.info(["fr"])
    assert exc.value.code is ErrorCode.UNSUPPORTED_LANGUAGE


@pytest.mark.models
@pytest.mark.tesseract
def test_tesseract_reads_english_page() -> None:
    engine = TesseractEngine(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 120, None)
    assert engine.unavailable_reason() is None
    lines = engine.recognize(text_page(dpi=200), ["en"])
    assert len(lines) > 30
    assert all(line.confidence is not None for line in lines)
    info = engine.info(["en"])
    assert info.version.startswith("tesseract 5.")
    assert info.models[0].id == "tesseract/eng"


@pytest.mark.models
@pytest.mark.tesseract
def test_tesseract_blank_page_is_empty() -> None:
    engine = TesseractEngine(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 120, None)
    assert engine.recognize(blank_page(dpi=100), ["en"]) == []


@pytest.mark.models
@pytest.mark.tesseract
def test_tesseract_matches_ground_truth_closely() -> None:
    from tests.fixtures.pages import text_page_with_truth

    image, truth = text_page_with_truth(dpi=200)
    engine = TesseractEngine(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 120, None)
    text = "\n".join(line.text for line in engine.recognize(image, ["en"]))
    assert cer(truth, text) < 0.05


@pytest.mark.models
@pytest.mark.tesseract
def test_non_tsv_output_is_a_failure_not_an_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    real_run = subprocess.run

    def plain_text_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "--psm" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"some text\n", stderr=b"")
        return real_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "run", plain_text_run)
    engine = TesseractEngine(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 120, None)
    with pytest.raises(AppError) as exc:
        engine.recognize(text_page(dpi=100), ["en"])
    assert exc.value.code is ErrorCode.OCR_FAILED


def test_word_final_joiners_are_removed_but_mid_word_joiners_kept() -> None:
    assert clean_text("பணம்\u200c") == "பணம்"
    assert clean_text("வேண்டும்\u200c.") == "வேண்டும்."
    assert clean_text("a\u200c b") == "a b"
    # Mid-word ZWNJ/ZWJ changes conjunct rendering in Indic scripts: must be preserved.
    assert clean_text("क्\u200cष") == "क्\u200cष"
    assert clean_text("र्\u200dय") == "र्\u200dय"
