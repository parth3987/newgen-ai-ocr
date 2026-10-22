"""End-to-end ingestion through the HTTP API with real local storage."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import MULTIPART_OVERHEAD_BYTES, create_app
from newgen_ocr.config import Settings
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf, image_bytes, jpeg_bytes

UPLOAD = "/api/v1/documents"


def _upload(client: TestClient, data: bytes, filename: str, content_type: str = "") -> dict:  # type: ignore[type-arg]
    response = client.post(UPLOAD, files={"file": (filename, data, content_type)})
    return {"status": response.status_code, "body": response.json(), "headers": response.headers}


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def test_native_pdf_end_to_end(client: TestClient) -> None:
    data = build_pdf([PageSpec(PARAGRAPH), PageSpec("Second page with enough native text.")])
    result = _upload(client, data, "invoice.pdf", "application/pdf")
    assert result["status"] == 201
    manifest = result["body"]
    doc = manifest["document_id"]
    assert manifest["page_count"] == 2
    assert all(p["needs_ocr"] is False for p in manifest["pages"])
    assert all(p["image"] is None for p in manifest["pages"])

    assert client.get(f"{UPLOAD}/{doc}").json() == manifest

    text = client.get(f"{UPLOAD}/{doc}/pages/2/text").json()
    assert text["page_number"] == 2
    assert text["text"].startswith("Second page")
    assert text["segments"][0]["bbox"]["x0"] > 0

    image = client.get(f"{UPLOAD}/{doc}/pages/1/image")
    assert image.status_code == 404
    assert image.json()["error"]["code"] == "PAGE_IMAGE_NOT_AVAILABLE"


def test_scanned_pdf_end_to_end(client: TestClient, settings: Settings) -> None:
    data = build_pdf([PageSpec(jpeg=jpeg_bytes()), PageSpec(jpeg=jpeg_bytes(), size=(300, 400))])
    result = _upload(client, data, "scan.pdf")
    assert result["status"] == 201
    doc = result["body"]["document_id"]
    pages = result["body"]["pages"]
    assert [p["needs_ocr"] for p in pages] == [True, True]
    dpi = settings.render_dpi
    # PDFium rounds to the nearest pixel: 400pt at 300 DPI = 1666.67 -> 1667.
    expected = (round(300 * dpi / 72), round(400 * dpi / 72))
    assert pages[1]["image"] == {"width_px": expected[0], "height_px": expected[1], "dpi": dpi}

    response = client.get(f"{UPLOAD}/{doc}/pages/2/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(response.content)) as img:
        assert img.size == expected

    empty_text = client.get(f"{UPLOAD}/{doc}/pages/1/text").json()
    assert empty_text["text"] == ""
    assert empty_text["segments"] == []


@pytest.mark.parametrize(
    ("pil_fmt", "filename", "pages"),
    [("JPEG", "a.jpg", 1), ("PNG", "a.png", 1), ("TIFF", "a.tiff", 3)],
)
def test_image_uploads(client: TestClient, pil_fmt: str, filename: str, pages: int) -> None:
    result = _upload(client, image_bytes(pil_fmt, frames=pages), filename)
    assert result["status"] == 201
    assert result["body"]["page_count"] == pages
    doc = result["body"]["document_id"]
    for n in range(1, pages + 1):
        assert client.get(f"{UPLOAD}/{doc}/pages/{n}/image").status_code == 200


@pytest.mark.parametrize(
    ("data", "filename", "status", "code"),
    [
        (b"", "empty.pdf", 422, "EMPTY_FILE"),
        (b"MZ\x90\x00executable", "setup.exe", 415, "UNSUPPORTED_FORMAT"),
        (image_bytes("GIF"), "anim.gif", 415, "UNSUPPORTED_FORMAT"),
        (image_bytes("PNG"), "renamed.pdf", 415, "FORMAT_MISMATCH"),
        (b"%PDF-1.4\n garbage", "bad.pdf", 422, "CORRUPT_DOCUMENT"),
        (build_pdf([PageSpec("x")], user_password="pw"), "locked.pdf", 422, "ENCRYPTED_DOCUMENT"),
        (image_bytes("PNG")[:70], "cut.png", 422, "CORRUPT_DOCUMENT"),
    ],
)
def test_rejected_uploads(
    client: TestClient,
    settings: Settings,
    data: bytes,
    filename: str,
    status: int,
    code: str,
) -> None:
    result = _upload(client, data, filename)
    assert result["status"] == status
    assert result["body"]["error"]["code"] == code
    assert result["body"]["error"]["request_id"] == result["headers"]["x-request-id"]
    for tree in ("quarantine", "staging", "originals", "derived"):
        assert _files(settings.data_dir / tree) == []


def test_missing_file_field(client: TestClient) -> None:
    response = client.post(UPLOAD, data={"other": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_oversized_upload_rejected(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, max_upload_bytes=10_000, _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        # Within the multipart allowance: rejected by the ingestion stream limit.
        small_over = client.post(UPLOAD, files={"file": ("a.pdf", b"%PDF-" + b"0" * 20_000)})
        # Beyond it: rejected by the request body limit before parsing.
        big = b"%PDF-" + b"0" * (10_000 + MULTIPART_OVERHEAD_BYTES + 1)
        far_over = client.post(UPLOAD, files={"file": ("a.pdf", big)})
    for response in (small_over, far_over):
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert _files(tmp_path / "quarantine") == []


def test_streamed_body_without_content_length_is_limited(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, max_upload_bytes=1_000, _env_file=None)  # type: ignore[call-arg]

    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(200):
            yield b"0" * 1024

    with TestClient(create_app(settings)) as client:
        response = client.post(
            UPLOAD,
            content=chunks(),
            headers={"content-type": "multipart/form-data; boundary=xyz"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/v1/documents/" + "0" * 32, 404, "DOCUMENT_NOT_FOUND"),
        ("/api/v1/documents/not-a-valid-id", 422, "VALIDATION_ERROR"),
        # Encoded "/" decodes to extra path segments: no route matches.
        ("/api/v1/documents/..%2F..%2Fetc", 404, "NOT_FOUND"),
    ],
)
def test_retrieval_errors(client: TestClient, path: str, status: int, code: str) -> None:
    response = client.get(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_page_out_of_range(client: TestClient) -> None:
    doc = _upload(client, image_bytes("PNG"), "a.png")["body"]["document_id"]
    for n in (0, 2):
        response = client.get(f"{UPLOAD}/{doc}/pages/{n}/image")
        assert response.status_code in (404, 422)
    assert client.get(f"{UPLOAD}/{doc}/pages/2/image").json()["error"]["code"] == "PAGE_NOT_FOUND"
