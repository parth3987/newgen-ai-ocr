"""FastAPI application factory.

Run with: uvicorn newgen_ocr.api.app:create_app --factory
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from newgen_ocr import __version__
from newgen_ocr.api.middleware import request_context_middleware
from newgen_ocr.api.routes import health
from newgen_ocr.config import Settings, get_settings
from newgen_ocr.errors import register_error_handlers
from newgen_ocr.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title="New Gen AI OCR",
        version=__version__,
        # Swagger UI / ReDoc load JS and CSS from public CDNs, which violates the
        # no-egress policy (ADR-0005). The raw OpenAPI schema stays available.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = settings

    app.middleware("http")(request_context_middleware)
    register_error_handlers(app)
    app.include_router(health.router)

    logger.info(
        "application created",
        extra={"environment": settings.environment, "version": __version__},
    )
    return app
