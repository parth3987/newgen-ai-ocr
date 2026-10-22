"""OCR through the HTTP API."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.errors import ErrorCode
from newgen_ocr.evaluation.corpus import DEFAULT_FONT_DIR, LANGUAGE_FONTS, load_lines, render_page
from newgen_ocr.evaluation.metrics import cer
from newgen_ocr.ocr.service import OcrService
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf
from tests.fixtures.ocr import FakeEngine
from tests.fixtures.pages import text_page

DOCS = "/api/v1/documents"
A4 = (595, 842)


def _jpeg(page: object) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(page).save(buf, "JPEG", quality=92)  # type: ignore[arg-type]
    return buf.getvalue()


SCAN_PDF = build_pdf([PageSpec(PARAGRAPH), PageSpec(jpeg=_jpeg(text_page(dpi=100)), size=A4)])


@pytest.fixture
def ocr_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    return Settings(  # type: ignore[call-arg]
        environment="test",
        data_dir=data,
        render_dpi=150,
        models_dir=MODELS_DIR,
        models_lock=MODELS_LOCK,
        _env_file=None,
    )


@pytest.fixture
def client(ocr_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(ocr_settings), raise_server_exceptions=False) as c:
        yield c


def _fake(client: TestClient, **engines: FakeEngine) -> None:
    """Swap the app's engines for fakes (keeps the real service, store and policy)."""
    service: OcrService = client.app.state.ocr  # type: ignore[attr-defined]
    service.engines = dict(engines)


def _upload(client: TestClient, data: bytes = SCAN_PDF) -> str:
    response = client.post(DOCS, files={"file": ("doc.pdf", data)})
    assert response.status_code == 201, response.text
    return str(response.json()["document_id"])


def test_ocr_run_lifecycle(client: TestClient) -> None:
    _fake(client, paddle=FakeEngine("paddle", "invoice 42"), tesseract=FakeEngine("tesseract"))
    doc = _upload(client)
    response = client.post(f"{DOCS}/{doc}/ocr", json={"languages": ["en"]})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["requested_engine"] == "auto"
    assert run["preprocessing_profile"] == "standard"
    native, scan = run["pages"]
    assert (native["status"], native["skip_reason"]) == ("skipped", "native_text")
    assert (scan["status"], scan["engine"], scan["text"]) == ("ok", "paddle", "invoice 42")
    assert scan["lines"][0]["bbox"] == {"x0": 10.0, "y0": 10.0, "x1": 200.0, "y1": 35.0}
    assert client.get(f"{DOCS}/{doc}/ocr").json() == ["auto"]
    assert client.get(f"{DOCS}/{doc}/ocr/auto").json() == run


def test_compare_endpoint(client: TestClient) -> None:
    _fake(client, paddle=FakeEngine("paddle", "abc"), tesseract=FakeEngine("tesseract", "xyz"))
    doc = _upload(client)
    assert client.post(f"{DOCS}/{doc}/ocr", json={"compare": True}).status_code == 201
    comparison = client.get(f"{DOCS}/{doc}/ocr/comparison").json()
    assert comparison["primary"] == "auto"
    assert comparison["secondary"] == "tesseract"
    assert comparison["pages"][1]["needs_review"] is True
    assert client.get(f"{DOCS}/{doc}/ocr").json() == ["auto", "tesseract"]


def test_engine_listing(client: TestClient) -> None:
    _fake(
        client,
        paddle=FakeEngine("paddle"),
        tesseract=FakeEngine("tesseract", unavailable="tesseract binary not found"),
    )
    engines = {e["name"]: e for e in client.get("/api/v1/ocr/engines").json()}
    assert engines["paddle"]["role"] == "primary"
    assert engines["paddle"]["available"] is True
    assert engines["tesseract"] == {
        "name": "tesseract",
        "role": "fallback",
        "available": False,
        "reason": "tesseract binary not found",
        "languages": ["en", "hi", "kn", "mr", "ta", "te"],
    }


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"languages": ["fr"]}, 422, ErrorCode.UNSUPPORTED_LANGUAGE),
        ({"engine": "cloudocr"}, 422, ErrorCode.UNKNOWN_ENGINE),
        ({"engine": "tesseract"}, 503, ErrorCode.OCR_ENGINE_UNAVAILABLE),
        ({"languages": []}, 422, ErrorCode.VALIDATION_ERROR),
        ({"engine": "Bad Name"}, 422, ErrorCode.VALIDATION_ERROR),
    ],
)
def test_ocr_request_errors(
    client: TestClient, body: dict[str, object], status: int, code: ErrorCode
) -> None:
    _fake(client, paddle=FakeEngine("paddle"), tesseract=FakeEngine("tesseract", unavailable="x"))
    doc = _upload(client)
    response = client.post(f"{DOCS}/{doc}/ocr", json=body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_missing_runs(client: TestClient) -> None:
    doc = _upload(client)
    for path in (f"{DOCS}/{doc}/ocr/paddle", f"{DOCS}/{doc}/ocr/comparison"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "OCR_RUN_NOT_FOUND"


def test_unknown_document(client: TestClient) -> None:
    response = client.post(f"{DOCS}/{'0' * 32}/ocr", json={})
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


@pytest.mark.models
def test_real_paddle_ocr_on_uploaded_telugu_scan(client: TestClient) -> None:
    lines = load_lines(ROOT / "evaluation" / "corpus" / "text", "te")[:4]
    page = render_page(lines, DEFAULT_FONT_DIR / LANGUAGE_FONTS["te"][0], 150)
    doc = _upload(client, build_pdf([PageSpec(jpeg=_jpeg(page), size=A4)]))
    response = client.post(f"{DOCS}/{doc}/ocr", json={"engine": "paddle", "languages": ["te"]})
    assert response.status_code == 201, response.text
    run = response.json()
    [page_result] = run["pages"]
    assert page_result["status"] == "ok"
    assert page_result["orientation_deg"] is None  # classifier is English-only (ADR-0021)
    assert cer("\n".join(lines), page_result["text"]) < 0.1
    [engine] = run["engines"]
    assert engine["name"] == "paddle"
    assert [m["id"] for m in engine["models"]][1] == "paddle/te_PP-OCRv5_mobile_rec"


@pytest.mark.models
@pytest.mark.tesseract
def test_real_auto_routes_kannada_to_tesseract_and_compares(client: TestClient) -> None:
    lines = load_lines(ROOT / "evaluation" / "corpus" / "text", "kn")[:4]
    page = render_page(lines, DEFAULT_FONT_DIR / LANGUAGE_FONTS["kn"][0], 200)
    doc = _upload(client, build_pdf([PageSpec(jpeg=_jpeg(page), size=A4)]))
    response = client.post(f"{DOCS}/{doc}/ocr", json={"languages": ["kn"], "compare": True})
    assert response.status_code == 201, response.text
    [result] = response.json()["pages"]
    assert result["engine"] == "tesseract"
    assert result["fallback_from"] is None
    assert result["lines"][0]["words"]  # Tesseract supplies word boxes
    assert cer("\n".join(lines), result["text"]) < 0.05
    comparison = client.get(f"{DOCS}/{doc}/ocr/comparison").json()
    assert comparison["secondary"] == "paddle"
    # PaddleOCR's Kannada model disagrees strongly, so the page is routed to review.
    assert comparison["pages"][0]["needs_review"] is True
