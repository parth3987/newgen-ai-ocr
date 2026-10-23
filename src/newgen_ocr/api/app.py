"""FastAPI application factory.

Run with: uvicorn newgen_ocr.api.app:create_app --factory
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI

from newgen_ocr import __version__
from newgen_ocr.api.body_limit import BodySizeLimitMiddleware
from newgen_ocr.api.errors import register_error_handlers
from newgen_ocr.api.middleware import request_context_middleware
from newgen_ocr.api.routes import (
    documents,
    extraction,
    health,
    jobs,
    layout,
    ocr,
    preprocessing,
    review,
    review_ui,
)
from newgen_ocr.config import Settings, get_settings
from newgen_ocr.db.engine import safe_url
from newgen_ocr.errors import AppError
from newgen_ocr.imaging.runtime import check_opencv_build, configure_opencv
from newgen_ocr.jobs.celery_app import dispatcher, make_celery, ping_broker
from newgen_ocr.jobs.service import JobService
from newgen_ocr.logging_config import configure_logging
from newgen_ocr.services import Services, build_engines, build_services

__all__ = ["build_engines", "create_app"]

logger = logging.getLogger(__name__)

# Allowance for multipart boundaries and part headers on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _owner_lookup(services: Services) -> Callable[[str], str | None]:
    """Who submitted a document: the database in service mode, the manifest otherwise."""
    if services.jobs is not None:
        return services.jobs.owner

    def from_manifest(document_id: str) -> str | None:
        try:
            return services.store.read_manifest(document_id).submitted_by
        except (AppError, OSError, ValueError):
            return None

    return from_manifest


def _services_mode(app: FastAPI, services: Services) -> None:
    """Service mode: jobs go to the workers through Valkey (or run inline when eager)."""
    app.state.jobs = None
    app.state.broker_ping = None
    if services.jobs is None:
        return
    if services.settings.jobs_eager:
        app.state.jobs = JobService(services, dispatch=None)
        return
    celery = make_celery(services.settings)
    app.state.jobs = JobService(services, dispatch=dispatcher(celery))
    app.state.broker_ping = lambda: ping_broker(celery)


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
    services = build_services(settings)
    app.state.services = services
    app.state.settings = settings
    app.state.store = services.store
    app.state.audit = services.audit
    app.state.tokens = services.tokens
    app.state.ingestion = services.ingestion
    app.state.preprocessing = services.preprocessing
    app.state.ocr = services.ocr
    app.state.layout = services.layout
    app.state.extraction = services.extraction
    app.state.review = services.review
    app.state.db = services.db
    app.state.owner_of = _owner_lookup(services)
    _services_mode(app, services)

    # Starlette runs the last-added middleware first: request context wraps the body limit.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    )
    app.middleware("http")(request_context_middleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(preprocessing.router)
    app.include_router(ocr.router)
    app.include_router(layout.router)
    app.include_router(extraction.router)
    app.include_router(review.router)
    app.include_router(review_ui.router)
    if app.state.jobs is not None:
        app.include_router(jobs.router)

    logger.info(
        "application created",
        extra={
            "environment": settings.environment,
            "version": __version__,
            "mode": "service" if settings.service_mode else "local",
            # Host, port and database name only: never the credentials.
            "database": safe_url(settings.database_url) if settings.database_url else None,
        },
    )
    return app
