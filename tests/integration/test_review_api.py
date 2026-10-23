"""Review over HTTP: authentication, roles, the full workflow, crops and the review page."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import create_app
from newgen_ocr.auth.tokens import Role, TokenStore
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.extraction_corpus import gst_invoice
from newgen_ocr.storage.local import DocumentStore
from tests.conftest import MODELS_DIR, MODELS_LOCK
from tests.fixtures.extraction import INVOICE, ITEMS, store_document, table
from tests.fixtures.review import calibration

DOCS = "/api/v1/documents"


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


def _tokens(settings: Settings) -> dict[str, dict[str, str]]:
    store = TokenStore(settings.auth_tokens_path)
    return {
        name: {"Authorization": f"Bearer {store.create(name, role)}"}
        for name, role in (
            ("vic", Role.VIEWER),
            ("asha", Role.REVIEWER),
            ("meena", Role.APPROVER),
            ("root", Role.ADMIN),
        )
    }


def _events(settings: Settings) -> list[dict[str, object]]:
    path = settings.data_dir / "audit" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_authentication_and_roles(client: TestClient, settings: Settings) -> None:
    tokens = _tokens(settings)
    missing = client.get("/api/v1/review/items")
    assert missing.status_code == 401 and missing.headers["WWW-Authenticate"] == "Bearer"
    assert missing.json()["error"]["code"] == "UNAUTHENTICATED"
    bad = client.get("/api/v1/review/items", headers={"Authorization": "Bearer ngocr_x"})
    assert bad.status_code == 401
    me = client.get("/api/v1/auth/whoami", headers=tokens["asha"]).json()
    assert (me["user"], me["role"]) == ("asha", "reviewer")
    denied = client.get("/api/v1/review/patterns", headers=tokens["asha"])
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "FORBIDDEN")
    assert client.get("/api/v1/review/history/verify", headers=tokens["root"]).json()["valid"]
    policy = client.get("/api/v1/review/policy", headers=tokens["vic"]).json()
    assert policy == {"auto_accept": False, "require_second_person": True, "calibration": None}
    events = _events(settings)
    failed = [e for e in events if e["event"] == "auth.failed"]
    assert {e["reason"] for e in failed} == {"missing", "malformed"}
    assert "ngocr_x" not in json.dumps(events)  # tokens are never logged
    assert any(e["event"] == "auth.denied" and e["actor"] == "asha" for e in events)


def test_workflow_over_http(client: TestClient, settings: Settings) -> None:
    tokens = _tokens(settings)
    doc = store_document(settings, DocumentStore(settings.data_dir), INVOICE, [table(ITEMS)])
    assert client.post(f"{DOCS}/{doc}/extraction", json={}).status_code == 201
    queue = client.get("/api/v1/review/items", headers=tokens["vic"]).json()
    assert queue["total"] == 1 and queue["items"][0]["document_id"] == doc
    base = f"/api/v1/review/items/{doc}/auto"
    item = client.get(base, headers=tokens["vic"]).json()
    assert item["status"] == "pending" and item["assessment"]["calibration_id"] is None

    # A viewer cannot claim; the reviewer claims and confirms every value.
    assert (
        client.post(
            f"{base}/claim", json={"expected_version": 1}, headers=tokens["vic"]
        ).status_code
        == 403
    )
    item = client.post(f"{base}/claim", json={"expected_version": 1}, headers=tokens["asha"]).json()
    changes = [
        {"field": d["field"], "action": "confirm"}
        for d in item["assessment"]["fields"]
        if d["status"] in ("extracted", "missing")
    ] + [{"table": "line_items", "action": "confirm"}]
    response = client.post(
        f"{base}/corrections",
        json={"expected_version": item["version"], "changes": changes},
        headers=tokens["asha"],
    )
    assert response.status_code == 200, response.text
    item = response.json()
    stale = client.post(f"{base}/approve", json={"expected_version": 1}, headers=tokens["meena"])
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "REVIEW_CONFLICT")
    reviewer_cannot = client.post(
        f"{base}/approve", json={"expected_version": item["version"]}, headers=tokens["asha"]
    )
    assert reviewer_cannot.status_code == 403
    approved = client.post(
        f"{base}/approve", json={"expected_version": item["version"]}, headers=tokens["meena"]
    ).json()
    assert approved["status"] == "approved" and approved["decision"]["actor"] == "meena"
    result = client.get(f"{base}/result", headers=tokens["vic"]).json()
    assert result["record"]["grand_total"] == "1180.00" and result["unresolved"] == []
    history = client.get(f"{base}/history", headers=tokens["vic"]).json()
    assert [e["kind"] for e in history][-1] == "approved"
    assert {e["actor"] for e in history} == {"system", "asha", "meena"}

    crop = client.get(f"{base}/crop", params={"field": "grand_total"}, headers=tokens["vic"])
    assert crop.headers["content-type"] == "image/png" and crop.content[:4] == b"\x89PNG"
    page = client.get(f"{base}/pages/1", params={"field": "grand_total"}, headers=tokens["vic"])
    assert page.status_code == 200
    assert client.get(f"{base}/crop", headers=tokens["vic"]).status_code == 422
    patterns = client.get("/api/v1/review/patterns", headers=tokens["meena"]).json()
    assert patterns["items_decided"] == {"approved": 1}

    audit = [e for e in _events(settings) if str(e["event"]).startswith("review.")]
    assert [e["event"] for e in audit] == [
        "review.queued",
        "review.claimed",
        "review.corrected",
        "review.approved",
    ]
    assert audit[-1]["actor"] == "meena" and "1180" not in json.dumps(audit)


def test_review_page(client: TestClient) -> None:
    page = client.get("/review")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "default-src 'none'" in csp
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "https://" not in page.text  # no external assets
    script = client.get("/review/static/review.js")
    assert script.status_code == 200 and "innerHTML" not in script.text
    assert client.get("/review/static/other.js").status_code == 422


def test_auto_accept_configuration(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="calibration_file"):
        Settings(data_dir=settings.data_dir, review_auto_accept=True, _env_file=None)
    path = tmp_path / "calibration.json"
    path.write_text(calibration().model_dump_json())
    production = Settings(
        environment="production",
        data_dir=settings.data_dir,
        review_calibration_file=path,
        review_auto_accept=True,
        _env_file=None,
    )
    # The dev OpenCV wheel is refused in production first (ADR-0015); not under test here.
    monkeypatch.setattr("newgen_ocr.api.app.check_opencv_build", lambda environment: None)
    with pytest.raises(ValueError, match="synthetic"):
        create_app(production)


@pytest.mark.models
@pytest.mark.tesseract
def test_live_invoice_cross_checked_by_second_engine(tmp_path: Path, settings: Settings) -> None:
    live = Settings(  # type: ignore[call-arg]
        environment="test",
        data_dir=settings.data_dir,
        models_dir=MODELS_DIR,
        models_lock=MODELS_LOCK,
        _env_file=None,
    )
    image, truth = gst_invoice("intra", 200)
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, "PNG")
    with TestClient(create_app(live)) as client:
        tokens = _tokens(live)
        doc = client.post(DOCS, files={"file": ("inv.png", buf.getvalue(), "image/png")}).json()[
            "document_id"
        ]
        ocr = client.post(f"{DOCS}/{doc}/ocr", json={"languages": ["en"], "compare": True})
        assert ocr.status_code == 201
        assert client.post(f"{DOCS}/{doc}/layout", json={}).status_code == 201
        assert client.post(f"{DOCS}/{doc}/extraction", json={}).status_code == 201
        item = client.get(f"/api/v1/review/items/{doc}/auto", headers=tokens["vic"]).json()
    fields = {d["field"]: d for d in item["assessment"]["fields"]}
    engines = {d["engine"]["status"] for d in fields.values() if d["status"] == "extracted"}
    assert "agree" in engines and engines <= {"agree", "disagree", "no_reading"}
    total = fields["grand_total"]
    assert total["engine"]["engine"] == "tesseract" and "grand_total" in total["checks_passed"]
    assert not total["verified"] and "not_calibrated" in total["reasons"]
    assert item["status"] == "pending" and truth.fields["grand_total"] == "11800.00"
