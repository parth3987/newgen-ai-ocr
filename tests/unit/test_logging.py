from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from newgen_ocr.logging_config import ConsoleFormatter, JsonFormatter, request_id_var


def _record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_core_fields_and_extras() -> None:
    out = json.loads(JsonFormatter().format(_record(page_count=3)))
    assert out["level"] == "INFO"
    assert out["logger"] == "test.logger"
    assert out["msg"] == "hello"
    assert out["page_count"] == 3
    assert "ts" in out
    assert "request_id" not in out


def test_json_formatter_includes_request_id() -> None:
    token = request_id_var.set("rid-42")
    try:
        out = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)
    assert out["request_id"] == "rid-42"


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("x")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    out = json.loads(JsonFormatter().format(record))
    assert "ValueError" in out["exc_info"]


def test_console_formatter() -> None:
    token = request_id_var.set("rid-7")
    try:
        line = ConsoleFormatter().format(_record(status=200))
    finally:
        request_id_var.reset(token)
    assert line.startswith("INFO")
    assert "request_id=rid-7" in line
    assert "status=200" in line


def test_access_log_is_metadata_only(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    client.get("/health/live?token=SENSITIVE", headers={"X-Request-ID": "rid-access"})
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    access = [entry for entry in lines if entry["logger"] == "newgen_ocr.access"]
    assert len(access) == 1
    assert access[0]["path"] == "/health/live"
    assert access[0]["status"] == 200
    assert access[0]["request_id"] == "rid-access"
    assert "SENSITIVE" not in json.dumps(lines)
