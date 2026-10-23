"""Layout and tables through the HTTP API (fake models, plus a live run marked `models`)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.client import authorized
from newgen_ocr.evaluation.layout_corpus import table_page, table_specs
from newgen_ocr.layout.models import RegionType
from newgen_ocr.layout.service import LayoutService
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT
from tests.fixtures.layout import (
    TABLE,
    TITLE,
    FakeLayoutModels,
    cell_box,
    draw_page,
    region,
    text_box,
    tokens,
)
from tests.fixtures.ocr import FakeEngine

DOCS = "/api/v1/documents"


@pytest.fixture
def layout_settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    return Settings(  # type: ignore[call-arg]
        environment="test",
        data_dir=data,
        models_dir=MODELS_DIR,
        models_lock=MODELS_LOCK,
        _env_file=None,
    )


@pytest.fixture
def client(layout_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(layout_settings), raise_server_exceptions=False) as c:
        yield authorized(c)


def _png(image: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, "PNG")
    return buf.getvalue()


def _upload(client: TestClient, image: np.ndarray) -> str:
    response = client.post(DOCS, files={"file": ("t.png", _png(image), "image/png")})
    assert response.status_code == 201, response.text
    return str(response.json()["document_id"])


def test_status_reports_models(client: TestClient) -> None:
    body = client.get("/api/v1/layout/status").json()
    assert set(body) == {"available", "reason", "models"}
    if body["available"]:
        kinds = {m["name"] for m in body["models"]}
        assert kinds == {"layout_detection", "table_classification", "table_structure"}


def test_errors(client: TestClient) -> None:
    page = np.full((400, 400), 255, np.uint8)
    page[100:120, 50:300] = 0
    doc = _upload(client, page)
    assert client.get(f"{DOCS}/{doc}/layout").json() == []
    missing = client.get(f"{DOCS}/{doc}/layout/auto")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "LAYOUT_RUN_NOT_FOUND")
    no_ocr = client.post(f"{DOCS}/{doc}/layout", json={"ocr_run": "auto"})
    assert no_ocr.json()["error"]["code"] in ("OCR_RUN_NOT_FOUND", "LAYOUT_UNAVAILABLE")
    bad = client.post(f"{DOCS}/{doc}/layout", json={"ocr_run": "../x"})
    assert bad.status_code == 422
    assert client.get(f"{DOCS}/{'0' * 32}/layout").status_code == 404
    overlay = client.get(f"{DOCS}/{doc}/layout/auto/pages/1/overlay")
    assert overlay.json()["error"]["code"] == "LAYOUT_RUN_NOT_FOUND"


def test_layout_with_fake_models(client: TestClient) -> None:
    words = [text_box(r, c) for r in range(3) for c in range(3)]
    doc = _upload(client, draw_page(words))
    lines = "\n".join(f"w{i}" for i in range(9))
    service: LayoutService = client.app.state.layout  # type: ignore[attr-defined]
    service.models = FakeLayoutModels(
        regions=[region(RegionType.SECTION_TITLE, TITLE), region(RegionType.TABLE, TABLE)],
        structure_tokens=tokens([["", "", ""]] * 3),
        cell_boxes=[cell_box(r, c) for r in range(3) for c in range(3)],
    )
    # OCR with a fake engine so the run exists; its lines do not fall in the table.
    client.app.state.ocr.engines = {"paddle": FakeEngine("paddle", lines)}  # type: ignore[attr-defined]
    client.app.state.ocr.detector = None  # type: ignore[attr-defined]
    assert client.post(f"{DOCS}/{doc}/ocr", json={"engine": "paddle"}).status_code == 201
    response = client.post(f"{DOCS}/{doc}/layout", json={"ocr_run": "paddle"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ocr_run"] == "paddle"
    table = body["pages"][0]["tables"][0]
    assert (table["rows"], table["cols"], table["ruling_check"]) == (3, 3, "verified")
    assert client.get(f"{DOCS}/{doc}/layout").json() == ["paddle"]
    assert client.get(f"{DOCS}/{doc}/layout/paddle").json()["pages"][0]["tables"][0]["rows"] == 3
    overlay = client.get(f"{DOCS}/{doc}/layout/paddle/pages/1/overlay")
    assert overlay.status_code == 200
    assert overlay.headers["content-type"] == "image/png"
    assert client.get(f"{DOCS}/{doc}/layout/paddle/pages/0/overlay").status_code == 422


@pytest.mark.models
def test_live_table_with_merged_header(client: TestClient) -> None:
    spec = next(
        s for s in table_specs(ROOT / "evaluation" / "corpus" / "text") if s.id == "en-colspan"
    )
    image, truth = table_page(spec, ROOT / "evaluation" / "corpus" / "text", 200)
    doc = _upload(client, image)
    assert client.post(f"{DOCS}/{doc}/ocr", json={"languages": ["en"]}).status_code == 201
    response = client.post(f"{DOCS}/{doc}/layout", json={})
    assert response.status_code == 201, response.text
    page = response.json()["pages"][0]
    (table,) = page["tables"]
    expected = {(c.row, c.col, c.row_span, c.col_span) for c in truth.tables[0].cells}
    got = {(c["row"], c["col"], c["row_span"], c["col_span"]) for c in table["cells"]}
    assert got == expected
    assert table["ruling_check"] == "verified"
    texts = {c["text"] for c in table["cells"]}
    assert {"Sales (units)", "North", "1350", "18"} <= texts
    assert client.get(f"{DOCS}/{doc}/layout/auto/pages/1/overlay").status_code == 200
