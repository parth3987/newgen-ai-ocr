from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from newgen_ocr import __version__
from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings


def test_live_returns_ok_and_version(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_when_data_dir_writable(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"data_dir_writable": True}}


def test_not_ready_when_data_dir_missing(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path / "missing", _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"data_dir_writable": False}}


def test_generates_request_id_when_absent(client: TestClient) -> None:
    response = client.get("/health/live")
    assert len(response.headers["X-Request-ID"]) == 32


def test_propagates_valid_caller_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_replaces_unsafe_caller_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "bad id\twith spaces"})
    assert response.headers["X-Request-ID"] != "bad id\twith spaces"
    assert len(response.headers["X-Request-ID"]) == 32
