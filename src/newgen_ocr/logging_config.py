"""Structured logging.

Policy: log operational metadata only (ids, sizes, timings, status codes, error codes).
Never log document contents, extracted text or field values - pass only safe
metadata through ``extra=``.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from newgen_ocr.config import LogFormat, LogLevel

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_URL_LOGGING_LIBRARIES = ("httpx", "httpx2", "httpcore", "urllib3")

# Attributes present on every LogRecord; anything else came from ``extra=``.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName", "color_message"}  # color_message: uvicorn ANSI dup
)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RESERVED_ATTRS}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = f"{record.levelname:<8} {record.name}: {record.getMessage()}"
        request_id = request_id_var.get()
        if request_id is not None:
            line += f" request_id={request_id}"
        for key, value in _extra_fields(record).items():
            line += f" {key}={value}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _StdoutHandler(logging.StreamHandler[TextIO]):
    """Writes to whatever ``sys.stdout`` is at emit time, so later redirection is honoured."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def configure_logging(level: LogLevel, fmt: LogFormat) -> None:
    """Configure the root logger. Safe to call more than once."""
    handler = _StdoutHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Our middleware emits access logs; uvicorn's would duplicate them (and log query strings).
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True

    # HTTP client libraries log full URLs (including query strings) at INFO.
    for name in _URL_LOGGING_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
