from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from newgen_ocr.errors import AppError, ErrorCode


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/nope", headers={"X-Request-ID": "req-1"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Not Found", "request_id": "req-1"}
    }


def test_method_not_allowed(client: TestClient) -> None:
    response = client.post("/health/live")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_app_error_maps_to_code_and_status(app: FastAPI, client: TestClient) -> None:
    async def boom() -> None:
        raise AppError(ErrorCode.FILE_TOO_LARGE, "bad thing")

    app.add_api_route("/test/app-error", boom)
    response = client.get("/test/app-error")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert response.json()["error"]["message"] == "bad thing"


def test_validation_error_does_not_echo_input(app: FastAPI, client: TestClient) -> None:
    async def needs_int(n: int) -> int:
        return n

    app.add_api_route("/test/validate", needs_int)
    response = client.get("/test/validate", params={"n": "SECRET-VALUE"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "SECRET-VALUE" not in response.text
    assert "query.n" in body["error"]["message"]


def test_unhandled_exception_is_generic_500(app: FastAPI, client: TestClient) -> None:
    async def crash() -> None:
        raise RuntimeError("internal detail that must not leak")

    app.add_api_route("/test/crash", crash)
    response = client.get("/test/crash", headers={"X-Request-ID": "req-500"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error",
            "request_id": "req-500",
        }
    }
    assert "must not leak" not in response.text
    assert response.headers["X-Request-ID"] == "req-500"
