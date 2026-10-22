from __future__ import annotations

import io
import json
import stat
from pathlib import Path

import pytest
from PIL import Image

from newgen_ocr.audit import AuditLog
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import DocumentFormat
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf, image_bytes, jpeg_bytes


@pytest.fixture
def service(settings: Settings) -> IngestionService:
    return IngestionService(settings, DocumentStore(settings.data_dir), AuditLog(settings.data_dir))


def _audit(settings: Settings) -> list[dict[str, object]]:
    path = settings.data_dir / "audit" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_pdf_ingestion_stores_original_and_derived_separately(
    service: IngestionService, settings: Settings
) -> None:
    data = build_pdf([PageSpec(PARAGRAPH), PageSpec(jpeg=jpeg_bytes())])
    manifest = service.ingest(io.BytesIO(data), UploadInfo("Inv 1.pdf", "application/pdf"))

    doc = manifest.document_id
    original = settings.data_dir / "originals" / doc / "original.pdf"
    assert original.read_bytes() == data
    assert stat.S_IMODE(original.stat().st_mode) == 0o440
    derived = settings.data_dir / "derived" / doc
    assert (derived / "manifest.json").is_file()
    assert (derived / "text" / "0001.json").is_file()
    assert not (derived / "pages" / "0001.png").exists()  # native page: no render
    assert (derived / "pages" / "0002.png").is_file()
    assert _files(settings.data_dir / "quarantine") == []
    assert _files(settings.data_dir / "staging") == []

    assert manifest.format is DocumentFormat.PDF
    assert manifest.page_count == 2
    assert [p.needs_ocr for p in manifest.pages] == [False, True]
    assert manifest.original_filename == "Inv 1.pdf"
    assert len(manifest.sha256) == 64
    assert manifest.processing.pdfium_version


def test_image_ingestion(service: IngestionService, settings: Settings) -> None:
    manifest = service.ingest(io.BytesIO(image_bytes("PNG")), UploadInfo("a.png", "image/png"))
    assert manifest.format is DocumentFormat.PNG
    assert [p.needs_ocr for p in manifest.pages] == [True]
    page = settings.data_dir / "derived" / manifest.document_id / "pages" / "0001.png"
    with Image.open(page) as img:
        assert img.size == (200, 100)


def test_accepted_document_is_audited_without_filename(
    service: IngestionService, settings: Settings
) -> None:
    manifest = service.ingest(io.BytesIO(image_bytes("PNG")), UploadInfo("Secret-Name.png", None))
    [event] = _audit(settings)
    assert event["event"] == "document.accepted"
    assert event["document_id"] == manifest.document_id
    assert event["sha256"] == manifest.sha256
    assert event["page_count"] == 1
    assert "Secret-Name" not in json.dumps(event)


@pytest.mark.parametrize(
    ("data", "filename", "code"),
    [
        (b"", "x.pdf", ErrorCode.EMPTY_FILE),
        (b"plain text", "x.txt", ErrorCode.UNSUPPORTED_FORMAT),
        (image_bytes("PNG"), "x.pdf", ErrorCode.FORMAT_MISMATCH),
        (b"%PDF-1.7 broken", "x.pdf", ErrorCode.CORRUPT_DOCUMENT),
        (build_pdf([PageSpec("x")], user_password="pw"), "x.pdf", ErrorCode.ENCRYPTED_DOCUMENT),
        (image_bytes("JPEG")[:100], "x.jpg", ErrorCode.CORRUPT_DOCUMENT),
    ],
)
def test_rejections_leave_no_files_and_are_audited(
    service: IngestionService, settings: Settings, data: bytes, filename: str, code: ErrorCode
) -> None:
    with pytest.raises(AppError) as exc:
        service.ingest(io.BytesIO(data), UploadInfo(filename, None))
    assert exc.value.code is code
    for tree in ("quarantine", "staging", "originals", "derived"):
        assert _files(settings.data_dir / tree) == []
    [event] = _audit(settings)
    assert event["event"] == "document.rejected"
    assert event["error_code"] == code.value


def test_size_limit_enforced_while_streaming(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, max_upload_bytes=1000, _env_file=None)  # type: ignore[call-arg]
    service = IngestionService(settings, DocumentStore(tmp_path), AuditLog(tmp_path))
    with pytest.raises(AppError) as exc:
        service.ingest(io.BytesIO(b"%PDF-" + b"0" * 5000), UploadInfo(None, None))
    assert exc.value.code is ErrorCode.FILE_TOO_LARGE
    assert _files(tmp_path / "quarantine") == []


def test_disallowed_format(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        data_dir=tmp_path, allowed_formats=frozenset({DocumentFormat.PDF}), _env_file=None
    )
    service = IngestionService(settings, DocumentStore(tmp_path), AuditLog(tmp_path))
    with pytest.raises(AppError) as exc:
        service.ingest(io.BytesIO(image_bytes("PNG")), UploadInfo(None, None))
    assert exc.value.code is ErrorCode.UNSUPPORTED_FORMAT


def test_unexpected_failure_is_cleaned_up_and_audited(
    service: IngestionService, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.store, "commit", explode)
    with pytest.raises(RuntimeError):
        service.ingest(io.BytesIO(image_bytes("PNG")), UploadInfo(None, None))
    for tree in ("quarantine", "staging", "derived"):
        assert _files(settings.data_dir / tree) == []
    assert _audit(settings)[0]["error_code"] == "INTERNAL_ERROR"
