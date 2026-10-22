"""OCR service policy with fake engines: page selection, fallback, comparison, storage."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from newgen_ocr.audit import AuditLog
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.ocr.engine import OcrEngine
from newgen_ocr.ocr.models import DetectionStatus, PageOcrStatus, ReviewReason, ScriptDetection
from newgen_ocr.ocr.service import OcrService, ReviewPolicy, route_languages
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf
from tests.fixtures.ocr import FakeDetector, FakeEngine
from tests.fixtures.pages import blank_page, rotate, text_page

A4 = (595, 842)


def _jpeg(page: object) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(page).save(buf, "JPEG", quality=90)  # type: ignore[arg-type]
    return buf.getvalue()


@pytest.fixture
def store(settings: Settings) -> DocumentStore:
    return DocumentStore(settings.data_dir)


@pytest.fixture
def doc(settings: Settings, store: DocumentStore) -> str:
    """Page 1 native text, page 2 skewed scan, page 3 blank scan."""
    pdf = build_pdf(
        [
            PageSpec(PARAGRAPH),
            PageSpec(jpeg=_jpeg(rotate(text_page(dpi=100), 3.0)), size=A4),
            PageSpec(jpeg=_jpeg(blank_page(dpi=60)), size=A4),
        ]
    )
    audit = AuditLog(settings.data_dir)
    s = settings.model_copy(update={"render_dpi": 100})
    manifest = IngestionService(s, store, audit).ingest(io.BytesIO(pdf), UploadInfo("d.pdf", None))
    PreprocessingService(store, audit, settings.preprocessing_profiles).run(
        manifest.document_id, "standard"
    )
    return manifest.document_id


def _service(store: DocumentStore, settings: Settings, **engines: OcrEngine) -> OcrService:
    return OcrService(
        store,
        AuditLog(settings.data_dir),
        dict(engines),
        primary="paddle",
        fallback="tesseract",
        disagreement_threshold=0.85,
    )


def test_only_pages_needing_ocr_are_read(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    paddle = FakeEngine("paddle", "line one\nline two")
    run = _service(store, settings, paddle=paddle, tesseract=FakeEngine("tesseract")).run(
        doc, "auto", ["en"], "standard"
    )
    native, scan, blank = run.pages
    assert (native.status, native.skip_reason) == (PageOcrStatus.SKIPPED, "native_text")
    assert (blank.status, blank.skip_reason) == (PageOcrStatus.SKIPPED, "blank_page")
    assert scan.status is PageOcrStatus.OK
    assert scan.engine == "paddle"
    assert scan.text == "line one\nline two"
    assert scan.mean_confidence == pytest.approx(0.9)
    assert len(paddle.calls) == 1
    assert [e.name for e in run.engines] == ["paddle"]


def test_preprocessed_image_is_used_when_available(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    service = _service(store, settings, paddle=FakeEngine("paddle"), tesseract=FakeEngine("t"))
    with_profile = service.run(doc, "auto", ["en"], "standard")
    without = service.run(doc, "paddle", ["en"], None)
    assert with_profile.pages[1].image_source == "preprocessed:standard"
    assert without.pages[1].image_source == "source"
    # Deskewing enlarges the canvas, so the two images differ in size.
    assert with_profile.pages[1].image_width != without.pages[1].image_width


def test_unknown_profile_falls_back_to_source(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    run = _service(store, settings, paddle=FakeEngine("paddle")).run(doc, "paddle", ["en"], "photo")
    assert run.preprocessing_profile is None
    assert run.pages[1].image_source == "source"


def test_fallback_on_primary_failure_is_recorded(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", fail_with=ErrorCode.OCR_FAILED),
        tesseract=FakeEngine("tesseract", "from tesseract"),
    )
    page = service.run(doc, "auto", ["en"], "standard").pages[1]
    assert page.status is PageOcrStatus.OK
    assert page.engine == "tesseract"
    assert (page.fallback_from, page.fallback_reason) == ("paddle", "OCR_FAILED")
    assert page.text == "from tesseract"


def test_fallback_when_primary_unavailable(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", unavailable="models missing"),
        tesseract=FakeEngine("tesseract"),
    )
    page = service.run(doc, "auto", ["en"], None).pages[1]
    assert page.engine == "tesseract"
    assert page.fallback_reason == "primary_unavailable"


def test_explicit_engine_never_falls_back(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    tesseract = FakeEngine("tesseract")
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", fail_with=ErrorCode.OCR_TIMEOUT),
        tesseract=tesseract,
    )
    page = service.run(doc, "paddle", ["en"], None).pages[1]
    assert (page.status, page.error_code) == (PageOcrStatus.FAILED, "OCR_TIMEOUT")
    assert tesseract.calls == []


def test_both_engines_failing(store: DocumentStore, settings: Settings, doc: str) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", fail_with=ErrorCode.OCR_FAILED),
        tesseract=FakeEngine("tesseract", fail_with=ErrorCode.OCR_TIMEOUT),
    )
    page = service.run(doc, "auto", ["en"], None).pages[1]
    assert (page.status, page.error_code) == (PageOcrStatus.FAILED, "OCR_TIMEOUT")


def test_empty_result(store: DocumentStore, settings: Settings, doc: str) -> None:
    page = (
        _service(store, settings, paddle=FakeEngine("paddle", ""))
        .run(doc, "paddle", ["en"], None)
        .pages[1]
    )
    assert page.status is PageOcrStatus.EMPTY
    assert page.lines == []
    assert page.mean_confidence is None


@pytest.mark.parametrize(
    ("engine", "languages", "code"),
    [
        ("paddle", ["fr"], ErrorCode.UNSUPPORTED_LANGUAGE),
        ("paddle", [], ErrorCode.UNSUPPORTED_LANGUAGE),
        ("easyocr", ["en"], ErrorCode.UNKNOWN_ENGINE),
    ],
)
def test_request_validation(
    store: DocumentStore,
    settings: Settings,
    doc: str,
    engine: str,
    languages: list[str],
    code: ErrorCode,
) -> None:
    with pytest.raises(AppError) as exc:
        _service(store, settings, paddle=FakeEngine("paddle")).run(doc, engine, languages, None)
    assert exc.value.code is code


def test_engine_without_language_model(store: DocumentStore, settings: Settings, doc: str) -> None:
    service = _service(store, settings, paddle=FakeEngine("paddle", languages=["en"]))
    with pytest.raises(AppError) as exc:
        service.run(doc, "paddle", ["te"], None)
    assert exc.value.code is ErrorCode.UNSUPPORTED_LANGUAGE


def test_no_engine_available(store: DocumentStore, settings: Settings, doc: str) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", unavailable="x"),
        tesseract=FakeEngine("tesseract", unavailable="y"),
    )
    with pytest.raises(AppError) as exc:
        service.run(doc, "auto", ["en"], None)
    assert exc.value.code is ErrorCode.OCR_ENGINE_UNAVAILABLE


def test_comparison_flags_disagreement_and_never_merges(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", "invoice total 1200"),
        tesseract=FakeEngine("tesseract", "lnvoice tota1 12OO and more"),
    )
    run = service.run(doc, "auto", ["en"], None, compare=True)
    assert run.pages[1].text == "invoice total 1200"  # primary result is the record
    comparison = store.read_ocr_comparison(doc)
    native, scan, _ = comparison.pages
    assert native.reason == "skipped:native_text"
    assert scan.needs_review is True
    assert scan.reason == "disagreement"
    assert scan.agreement is not None and scan.agreement < 0.85
    assert store.list_ocr_runs(doc) == ["auto", "tesseract"]


def test_comparison_agreement(store: DocumentStore, settings: Settings, doc: str) -> None:
    service = _service(
        store,
        settings,
        paddle=FakeEngine("paddle", "same"),
        tesseract=FakeEngine("tesseract", "same"),
    )
    service.run(doc, "auto", ["en"], None, compare=True)
    page = store.read_ocr_comparison(doc).pages[1]
    assert (page.agreement, page.needs_review, page.reason) == (1.0, False, "agree")


def test_run_is_stored_and_audited(store: DocumentStore, settings: Settings, doc: str) -> None:
    run = _service(store, settings, paddle=FakeEngine("paddle")).run(doc, "paddle", ["hi"], None)
    assert store.read_ocr_run(doc, "paddle") == run
    assert run.languages == ["hi"]
    assert run.runtime.cpu_model
    events = [
        json.loads(line)
        for line in (settings.data_dir / "audit" / "audit.jsonl").read_text().splitlines()
    ]
    ocr_events = [e for e in events if e["event"] == "ocr.completed"]
    assert ocr_events[-1]["engines_used"] == "paddle"
    assert ocr_events[-1]["pages_ok"] == 1


def test_missing_run(store: DocumentStore, doc: str) -> None:
    with pytest.raises(AppError) as exc:
        store.read_ocr_run(doc, "tesseract")
    assert exc.value.code is ErrorCode.OCR_RUN_NOT_FOUND


def test_settings_reject_same_primary_and_fallback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must differ"):
        Settings(  # type: ignore[call-arg]
            data_dir=tmp_path,
            ocr_primary_engine="paddle",
            ocr_fallback_engine="paddle",
            _env_file=None,
        )


def test_language_override_swaps_primary_and_fallback(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    paddle, tesseract = FakeEngine("paddle", "from paddle"), FakeEngine("tesseract", "from tess")
    service = OcrService(
        store,
        AuditLog(settings.data_dir),
        {"paddle": paddle, "tesseract": tesseract},
        primary="paddle",
        fallback="tesseract",
        disagreement_threshold=0.85,
        language_primary={"te": "tesseract"},
    )
    assert service.run(doc, "auto", ["te"], None).pages[1].engine == "tesseract"
    assert service.run(doc, "auto", ["ta"], None).pages[1].engine == "paddle"
    # The swapped fallback is used when the overriding primary fails.
    tesseract.fail_with = ErrorCode.OCR_FAILED
    page = service.run(doc, "auto", ["te"], None).pages[1]
    assert (page.engine, page.fallback_from) == ("paddle", "tesseract")
    # Comparison uses the other engine as secondary.
    tesseract.fail_with = None
    service.run(doc, "auto", ["te"], None, compare=True)
    assert store.read_ocr_comparison(doc).secondary == "paddle"


def test_kannada_never_uses_paddle_even_as_fallback(
    store: DocumentStore, settings: Settings, doc: str
) -> None:
    """The language matrix enables only Tesseract for Kannada (PaddleOCR measured CER 0.39)."""
    paddle = FakeEngine("paddle")
    tesseract = FakeEngine("tesseract", fail_with=ErrorCode.OCR_FAILED)
    service = _service(store, settings, paddle=paddle, tesseract=tesseract)
    service.language_primary = {"kn": "tesseract"}
    page = service.run(doc, "auto", ["kn"], None).pages[1]
    assert (page.status, page.error_code) == (PageOcrStatus.FAILED, "OCR_FAILED")
    assert paddle.calls == []
    with pytest.raises(AppError) as exc:
        service.run(doc, "paddle", ["kn"], None)
    assert exc.value.code is ErrorCode.UNSUPPORTED_LANGUAGE


def test_default_settings_route_kannada_to_tesseract(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    assert settings.ocr_language_primary == {"kn": "tesseract"}


# ---------------------------------------------------------------- Phase 5: languages, review


@pytest.fixture
def doc200(settings: Settings, store: DocumentStore) -> str:
    """Like `doc`, but rendered at 200 DPI so no low-resolution flag masks other reasons."""
    pdf = build_pdf(
        [
            PageSpec(PARAGRAPH),
            PageSpec(jpeg=_jpeg(text_page(dpi=200)), size=A4),
            PageSpec(jpeg=_jpeg(blank_page(dpi=60)), size=A4),
        ]
    )
    audit = AuditLog(settings.data_dir)
    s = settings.model_copy(update={"render_dpi": 200})
    manifest = IngestionService(s, store, audit).ingest(io.BytesIO(pdf), UploadInfo("d.pdf", None))
    return manifest.document_id


def _detection(**kwargs: object) -> ScriptDetection:
    return ScriptDetection(status=DetectionStatus.DETECTED, **kwargs)  # type: ignore[arg-type]


def _phase5(
    store: DocumentStore,
    settings: Settings,
    detection: ScriptDetection | None,
    *,
    min_page: float = 0.85,
    min_line: float = 0.70,
    detect_orientation: bool = True,
    **engines: OcrEngine,
) -> OcrService:
    detector = FakeDetector(detection) if detection is not None else None
    return OcrService(
        store,
        AuditLog(settings.data_dir),
        dict(engines),
        primary="paddle",
        fallback="tesseract",
        disagreement_threshold=0.85,
        language_primary={"kn": "tesseract"},
        detector=detector,  # type: ignore[arg-type]
        detect_orientation=detect_orientation,
        review_policy=ReviewPolicy(min_page_confidence=min_page, min_line_confidence=min_line),
    )


def test_route_languages() -> None:
    assert route_languages("Telugu", []) == (["te", "en"], "")
    assert route_languages("Devanagari", []) == (["hi", "mr", "en"], "")
    # English-dominant page with Devanagari lines: the Devanagari recogniser also reads Latin.
    assert route_languages("Latin", ["Devanagari"]) == (["hi", "mr", "en"], "")
    languages, note = route_languages("Telugu", ["Tamil"])
    assert languages[:2] == ["te", "ta"]
    assert note == "multiple Indic scripts: Telugu, Tamil"


def test_auto_routes_page_by_detected_script(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    paddle, tesseract = FakeEngine("paddle", "తెలుగు వచనం"), FakeEngine("tesseract")
    service = _phase5(
        store,
        settings,
        _detection(script="Telugu", script_confidence=0.98),
        paddle=paddle,
        tesseract=tesseract,
    )
    run = service.run(doc200, "auto", ["auto"], None)
    page = run.pages[1]
    assert page.languages == ["te", "en"]
    assert page.engine == "paddle"
    assert paddle.calls[0][1] == ("te", "en")
    assert page.detected_language is not None and page.detected_language.languages == ["te"]
    assert page.review.needs_review is False
    assert run.languages == ["auto"]


def test_auto_kannada_goes_to_tesseract(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    service = _phase5(
        store,
        settings,
        _detection(script="Kannada", script_confidence=1.0),
        paddle=FakeEngine("paddle"),
        tesseract=FakeEngine("tesseract", "ಕನ್ನಡ"),
    )
    page = service.run(doc200, "auto", ["auto"], None).pages[1]
    assert (page.engine, page.languages) == ("tesseract", ["kn", "en"])


@pytest.mark.parametrize(
    ("detection", "reason"),
    [
        (ScriptDetection(status=DetectionStatus.DETECTED, script="Bengali"), "unsupported_script"),
        (
            ScriptDetection(status=DetectionStatus.UNCERTAIN, census={"Tamil": 0.4}),
            "script_uncertain",
        ),
        (ScriptDetection(status=DetectionStatus.UNDETERMINED), "script_uncertain"),
    ],
)
def test_unrecognisable_pages_are_routed_to_review_not_guessed(
    store: DocumentStore, settings: Settings, doc200: str, detection: ScriptDetection, reason: str
) -> None:
    paddle = FakeEngine("paddle")
    service = _phase5(store, settings, detection, paddle=paddle, tesseract=FakeEngine("t"))
    run = service.run(doc200, "auto", ["auto"], None)
    page = run.pages[1]
    assert page.status is PageOcrStatus.UNSUPPORTED
    assert page.review.needs_review is True
    assert [r.value for r in page.review.reasons] == [reason]
    assert page.text == ""
    assert paddle.calls == []
    assert run.pages_needing_review == 1


def test_auto_requires_script_detection(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    service = _phase5(store, settings, None, paddle=FakeEngine("paddle"))
    with pytest.raises(AppError) as exc:
        service.run(doc200, "auto", ["auto"], None)
    assert exc.value.code is ErrorCode.OCR_ENGINE_UNAVAILABLE


def test_detected_orientation_is_applied(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    detection = ScriptDetection(
        status=DetectionStatus.DETECTED, orientation_deg=90, orientation_confidence=20.0
    )
    paddle = FakeEngine("paddle")
    service = _phase5(store, settings, detection, paddle=paddle)
    page = service.run(doc200, "paddle", ["en"], None).pages[1]
    assert page.image_rotation_deg == 90
    assert (page.image_width, page.image_height) == (paddle.calls[0][0][1], paddle.calls[0][0][0])
    # Explicit languages: orientation only, no script census.
    assert service.detector.calls == [False]  # type: ignore[union-attr]


def test_unreliable_orientation_is_not_applied(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    detection = ScriptDetection(
        status=DetectionStatus.UNDETERMINED, orientation_deg=180, orientation_confidence=0.8
    )
    page = (
        _phase5(store, settings, detection, paddle=FakeEngine("paddle"))
        .run(doc200, "paddle", ["en"], None)
        .pages[1]
    )
    assert page.image_rotation_deg == 0


def test_explicit_language_script_mismatch_from_engine_output(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    """A Devanagari recogniser on an English page returns mostly Latin text."""
    service = _phase5(store, settings, None, paddle=FakeEngine("paddle", "Total amount due"))
    page = service.run(doc200, "paddle", ["hi"], None).pages[1]
    assert ReviewReason.SCRIPT_MISMATCH in page.review.reasons


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.95, []), (0.80, ["low_confidence"]), (0.5, ["low_confidence", "low_confidence_lines"])],
)
def test_confidence_review(
    store: DocumentStore, settings: Settings, doc200: str, confidence: float, expected: list[str]
) -> None:
    engine = FakeEngine("paddle", "one\ntwo")
    engine.confidence = confidence
    page = (
        _phase5(store, settings, None, paddle=engine).run(doc200, "paddle", ["en"], None).pages[1]
    )
    assert [r.value for r in page.review.reasons] == expected


def test_empty_result_on_non_blank_page_needs_review(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    page = (
        _phase5(store, settings, None, paddle=FakeEngine("paddle", ""))
        .run(doc200, "paddle", ["en"], None)
        .pages[1]
    )
    assert page.review.reasons == [ReviewReason.EMPTY_RESULT]


def test_handwriting_english_is_experimental_and_always_reviewed(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    hw = FakeEngine("paddle_handwriting", "dear sir", languages=["en"])
    paddle = FakeEngine("paddle")
    service = _phase5(store, settings, None, paddle=paddle, paddle_handwriting=hw)
    run = service.run(doc200, "auto", ["en"], None, handwriting=True)
    page = run.pages[1]
    assert (page.engine, page.handwriting) == ("paddle_handwriting", True)
    assert page.review.reasons == [ReviewReason.HANDWRITING_UNVERIFIED]
    assert paddle.calls == []
    assert run.handwriting is True


@pytest.mark.parametrize("language", ["hi", "mr", "te", "ta", "kn"])
def test_indic_handwriting_is_unsupported(
    store: DocumentStore, settings: Settings, doc200: str, language: str
) -> None:
    hw = FakeEngine("paddle_handwriting", languages=["en"])
    service = _phase5(
        store,
        settings,
        None,
        paddle=FakeEngine("paddle"),
        paddle_handwriting=hw,
        tesseract=FakeEngine("tesseract"),
    )
    page = service.run(doc200, "auto", [language], None, handwriting=True).pages[1]
    assert page.status is PageOcrStatus.UNSUPPORTED
    assert page.review.reasons == [ReviewReason.HANDWRITING_UNSUPPORTED]
    assert hw.calls == []


def test_handwriting_rejects_compare_and_named_engines(
    store: DocumentStore, settings: Settings, doc200: str
) -> None:
    service = _phase5(store, settings, None, paddle=FakeEngine("paddle"))
    for kwargs in ({"compare": True}, {}):
        engine = "auto" if kwargs else "paddle"
        with pytest.raises(AppError):
            service.run(doc200, engine, ["en"], None, handwriting=True, **kwargs)  # type: ignore[arg-type]


def test_mixed_indic_scripts_flagged(store: DocumentStore, settings: Settings, doc200: str) -> None:
    detection = _detection(script="Telugu", secondary_scripts=["Tamil"], script_confidence=0.7)
    page = (
        _phase5(store, settings, detection, paddle=FakeEngine("paddle", "తెలుగు"))
        .run(doc200, "auto", ["auto"], None)
        .pages[1]
    )
    assert ReviewReason.MIXED_SCRIPT in page.review.reasons
