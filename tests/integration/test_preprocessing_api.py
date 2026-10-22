"""Preprocessing through the HTTP API with real storage."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from tests.fixtures.builders import PARAGRAPH, PageSpec, build_pdf
from tests.fixtures.pages import Gray, gaussian_noise, rotate, text_page

DOCS = "/api/v1/documents"
# The scan is A4-proportioned; placing it on a Letter page would distort its skew angle.
A4 = (595, 842)


@pytest.fixture
def pp_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    return Settings(environment="test", data_dir=data, render_dpi=200, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def client(pp_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(pp_settings), raise_server_exceptions=False) as c:
        yield c


def _jpeg(page: Gray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(page).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _png(page: Gray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(page).save(buf, "PNG", dpi=(200, 200))
    return buf.getvalue()


SKEWED_SCAN_PDF = build_pdf(
    [PageSpec(PARAGRAPH), PageSpec(jpeg=_jpeg(rotate(text_page(dpi=100), 4.0)), size=A4)]
)


def _upload(client: TestClient, data: bytes, name: str, profile: str | None = None) -> str:
    form = {"profile": profile} if profile else None
    response = client.post(DOCS, files={"file": (name, data)}, data=form)
    assert response.status_code == 201, response.text
    return str(response.json()["document_id"])


def _sha(client: TestClient, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return hashlib.sha256(response.content).hexdigest()


def _audit(settings: Settings) -> list[dict[str, object]]:
    lines = (settings.data_dir / "audit" / "audit.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_upload_runs_default_profile_and_records_quality(client: TestClient) -> None:
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf")
    manifest = client.get(f"{DOCS}/{doc}").json()
    assert manifest["pages"][0]["quality"] is None  # native text page: no image
    assert "skewed" in manifest["pages"][1]["quality"]["flags"]

    assert client.get(f"{DOCS}/{doc}/preprocessing").json() == ["standard"]
    run = client.get(f"{DOCS}/{doc}/preprocessing/standard").json()
    assert run["profile"]["name"] == "standard"
    assert run["runtime"]["cpu_model"]
    assert run["total_ms"] > 0
    native, scan = run["pages"]
    assert (native["status"], native["skip_reason"]) == ("skipped", "no_page_image")
    assert scan["status"] == "processed"
    assert scan["skew_corrected_deg"] == pytest.approx(4.0, abs=0.3)
    assert "skewed" not in scan["quality_after"]["flags"]

    image = client.get(f"{DOCS}/{doc}/preprocessing/standard/pages/2/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"


def test_source_page_is_preserved_across_reruns(client: TestClient) -> None:
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf")
    source_url = f"{DOCS}/{doc}/pages/2/image"
    before = _sha(client, source_url)
    standard = _sha(client, f"{DOCS}/{doc}/preprocessing/standard/pages/2/image")

    rerun = client.post(f"{DOCS}/{doc}/preprocessing/binarize")
    assert rerun.status_code == 201
    assert client.post(f"{DOCS}/{doc}/preprocessing/standard").status_code == 201

    assert _sha(client, source_url) == before
    assert client.get(f"{DOCS}/{doc}/preprocessing").json() == ["binarize", "standard"]
    assert _sha(client, f"{DOCS}/{doc}/preprocessing/standard/pages/2/image") == standard
    binarized = client.get(f"{DOCS}/{doc}/preprocessing/binarize/pages/2/image").content
    with Image.open(io.BytesIO(binarized)) as img:
        assert set(np.unique(np.asarray(img))) <= {0, 255}


def test_none_profile_leaves_page_unchanged(client: TestClient) -> None:
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf", profile="none")
    run = client.get(f"{DOCS}/{doc}/preprocessing/none").json()
    assert run["pages"][1]["status"] == "unchanged"
    response = client.get(f"{DOCS}/{doc}/preprocessing/none/pages/2/image")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PREPROCESSED_IMAGE_NOT_AVAILABLE"


def test_noisy_image_upload_is_denoised(client: TestClient) -> None:
    doc = _upload(client, _png(gaussian_noise(text_page(dpi=200), 20)), "noisy.png")
    [page] = client.get(f"{DOCS}/{doc}/preprocessing/standard").json()["pages"]
    applied = {s["name"]: s["reason"] for s in page["steps"] if s["applied"]}
    assert applied == {"denoise": "flag:noisy"}


def test_unknown_profile_on_upload_stores_nothing(
    client: TestClient, pp_settings: Settings
) -> None:
    response = client.post(
        DOCS, files={"file": ("a.pdf", SKEWED_SCAN_PDF)}, data={"profile": "sharpen_max"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNKNOWN_PROFILE"
    assert not (pp_settings.data_dir / "originals").exists()


def test_profile_listing(client: TestClient) -> None:
    names = [p["name"] for p in client.get("/api/v1/preprocessing/profiles").json()]
    assert names == ["none", "standard", "degraded_scan", "photo", "binarize"]


@pytest.mark.parametrize(
    ("method", "suffix", "status", "code"),
    [
        ("post", "/preprocessing/unknown", 422, "UNKNOWN_PROFILE"),
        ("get", "/preprocessing/photo", 404, "PREPROCESSING_NOT_FOUND"),
        ("get", "/preprocessing/Bad-Name", 422, "VALIDATION_ERROR"),
        ("get", "/preprocessing/standard/pages/9/image", 404, "PAGE_NOT_FOUND"),
    ],
)
def test_preprocessing_errors(
    client: TestClient, method: str, suffix: str, status: int, code: str
) -> None:
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf")
    response = getattr(client, method)(f"{DOCS}/{doc}{suffix}")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_preprocessing_failure_does_not_fail_upload(
    client: TestClient, pp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = client.app.state.preprocessing  # type: ignore[attr-defined]

    def explode(document_id: str, profile_name: str) -> None:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(service, "run", explode)
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf")
    assert client.get(f"{DOCS}/{doc}").status_code == 200
    events = [e["event"] for e in _audit(pp_settings)]
    assert events == ["document.accepted", "preprocessing.failed"]


def test_audit_records_preprocessing(client: TestClient, pp_settings: Settings) -> None:
    doc = _upload(client, SKEWED_SCAN_PDF, "scan.pdf")
    completed = [e for e in _audit(pp_settings) if e["event"] == "preprocessing.completed"]
    assert completed[0]["document_id"] == doc
    assert completed[0]["profile"] == "standard"
    assert completed[0]["pages_processed"] == 1


def test_production_refuses_pypi_opencv(tmp_path: Path) -> None:
    settings = Settings(environment="production", data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match="ADR-0015"):
        create_app(settings)
