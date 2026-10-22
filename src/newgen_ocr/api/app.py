"""FastAPI application factory.

Run with: uvicorn newgen_ocr.api.app:create_app --factory
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from newgen_ocr import __version__
from newgen_ocr.api.body_limit import BodySizeLimitMiddleware
from newgen_ocr.api.errors import register_error_handlers
from newgen_ocr.api.middleware import request_context_middleware
from newgen_ocr.api.routes import documents, health, preprocessing
from newgen_ocr.audit import AuditLog
from newgen_ocr.config import Settings, get_settings
from newgen_ocr.imaging.runtime import check_opencv_build, configure_opencv
from newgen_ocr.ingestion.service import IngestionService
from newgen_ocr.logging_config import configure_logging
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

# Allowance for multipart boundaries and part headers on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    check_opencv_build(settings.environment)
    configure_opencv(settings.opencv_threads)

    app = FastAPI(
        title="New Gen AI OCR",
        version=__version__,
        # Swagger UI / ReDoc load JS and CSS from public CDNs, which violates the
        # no-egress policy (ADR-0005). The raw OpenAPI schema stays available.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        # FastAPI's built-in OpenTelemetry instrumentation and environment-driven
        # OTLP export are switched off explicitly (ADR-0008).
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    store = DocumentStore(settings.data_dir)
    audit = AuditLog(settings.data_dir)
    app.state.settings = settings
    app.state.store = store
    app.state.ingestion = IngestionService(settings, store, audit)
    app.state.preprocessing = PreprocessingService(store, audit, settings.preprocessing_profiles)

    # Starlette runs the last-added middleware first: request context wraps the body limit.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    )
    app.middleware("http")(request_context_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(preprocessing.router)

    logger.info(
        "application created",
        extra={"environment": settings.environment, "version": __version__},
    )
    return app
