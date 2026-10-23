"""Liveness and readiness probes."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from newgen_ocr import __version__
from newgen_ocr.config import Settings
from newgen_ocr.db.__main__ import schema_is_current
from newgen_ocr.db.engine import Database

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


def _check_data_dir(settings: Settings) -> bool:
    path = settings.data_dir
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    """The process is up and serving requests. Performs no dependency checks."""
    return LivenessResponse(status="ok", version=__version__)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """All dependencies required to accept work are available: the data volume and, in
    service mode, PostgreSQL (at the latest migration) and the broker."""
    settings: Settings = request.app.state.settings
    checks = {"data_dir_writable": _check_data_dir(settings)}
    db: Database | None = request.app.state.db
    if db is not None:
        checks["database"] = await run_in_threadpool(db.ping)
        checks["database_schema_current"] = checks["database"] and await run_in_threadpool(
            schema_is_current, db
        )
    broker_ping = request.app.state.broker_ping
    if broker_ping is not None:
        checks["broker"] = await run_in_threadpool(broker_ping)
    if all(checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    response.status_code = 503
    return ReadinessResponse(status="not_ready", checks=checks)
