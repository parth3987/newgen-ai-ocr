"""Liveness and readiness probes."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from newgen_ocr import __version__
from newgen_ocr.config import Settings

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
    """All dependencies required to accept work are available."""
    settings: Settings = request.app.state.settings
    checks = {"data_dir_writable": _check_data_dir(settings)}
    if all(checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    response.status_code = 503
    return ReadinessResponse(status="not_ready", checks=checks)
