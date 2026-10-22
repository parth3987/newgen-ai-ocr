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
from newgen_ocr.ocr.models import PageOcrStatus
from newgen_ocr.ocr.service import OcrService
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf
from tests.fixtures.ocr import FakeEngine
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
        language_primary={"kn": "tesseract"},
    )
    assert service.run(doc, "auto", ["kn"], None).pages[1].engine == "tesseract"
    assert service.run(doc, "auto", ["te"], None).pages[1].engine == "paddle"
    # The swapped fallback is used when the overriding primary fails.
    tesseract.fail_with = ErrorCode.OCR_FAILED
    page = service.run(doc, "auto", ["kn"], None).pages[1]
    assert (page.engine, page.fallback_from) == ("paddle", "tesseract")
    # Comparison uses the other engine as secondary.
    tesseract.fail_with = None
    service.run(doc, "auto", ["kn"], None, compare=True)
    assert store.read_ocr_comparison(doc).secondary == "paddle"


def test_default_settings_route_kannada_to_tesseract(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    assert settings.ocr_language_primary == {"kn": "tesseract"}
