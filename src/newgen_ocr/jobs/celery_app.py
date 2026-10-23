"""Celery on Valkey (ADR-0029).

The broker only carries job ids: parameters, state and results live in PostgreSQL, and
documents on the data volume. Messages are acknowledged after the task returns and
requeued when a worker process dies, so a job is never lost with its worker; duplicates
are harmless because only a successful claim runs anything (``JobRunner``).

JSON serialisation only (no pickle); results are not stored in the broker.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable

from celery import Celery

from newgen_ocr.config import Settings, get_settings
from newgen_ocr.jobs.runner import JobRunner
from newgen_ocr.services import Services, build_services

logger = logging.getLogger(__name__)

QUEUE = "jobs"
RUN_JOB = "newgen_ocr.run_job"
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")

_services: Services | None = None
_lock = threading.Lock()


def services() -> Services:
    """Built lazily in each worker process (after the pool forks), then reused."""
    global _services
    with _lock:
        if _services is None:
            _services = build_services(get_settings(), pool_size=2)
        return _services


def make_celery(settings: Settings) -> Celery:
    if settings.broker_url is None:
        raise ValueError("broker_url is not set (service mode needs Valkey)")
    app = Celery("newgen_ocr", set_as_current=False)
    app.conf.update(
        broker_url=settings.broker_url.get_secret_value(),
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": settings.broker_visibility_timeout_s},
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        # Fail fast when the broker is down: the job is recorded and the reaper re-sends it.
        task_publish_retry_policy={
            "max_retries": 2,
            "interval_start": 0,
            "interval_step": 0.5,
            "interval_max": 1,
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_default_queue=QUEUE,
        task_routes={RUN_JOB: {"queue": QUEUE}},
        worker_prefetch_multiplier=1,
        worker_concurrency=settings.worker_concurrency,
        worker_hijack_root_logger=False,
        worker_redirect_stdouts=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
        enable_utc=True,
    )

    @app.task(name=RUN_JOB, ignore_result=True)  # type: ignore[untyped-decorator]
    def run_job(job_id: str) -> None:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            logger.warning("ignored a malformed job message")
            return
        JobRunner(services(), dispatcher(app)).run(job_id)

    return app


def dispatcher(app: Celery) -> Callable[[str, int], None]:
    def dispatch(job_id: str, delay_s: int = 0) -> None:
        app.send_task(RUN_JOB, args=[job_id], countdown=delay_s or None, queue=QUEUE)

    return dispatch


def ping_broker(app: Celery) -> bool:
    try:
        with app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=0, timeout=2)
        return True
    except Exception:  # noqa: BLE001 - readiness probe: any failure means not ready
        return False


def pending_messages(app: Celery) -> int | None:
    """Messages waiting in the job queue (None when the broker cannot be asked)."""
    try:
        with app.connection_for_read() as connection:
            declared = connection.default_channel.queue_declare(queue=QUEUE, passive=True)
            return int(declared.message_count)
    except Exception as exc:  # noqa: BLE001 - a broker error: unknown
        # Valkey removes an empty queue, so a missing one (AMQP 404) holds no messages.
        return 0 if str(getattr(exc, "reply_code", "")) == "404" else None


def reap_once(svc: Services, app: Celery) -> tuple[list[str], list[str]]:
    """Requeue jobs whose worker died, and queued jobs whose message was lost (only when
    the broker holds no waiting messages, so a long backlog is not flooded with copies)."""
    if svc.jobs is None:
        raise ValueError("reaping needs the database (service mode)")
    include_stale = pending_messages(app) == 0
    redispatch, failed = svc.jobs.reap(include_stale=include_stale)
    dispatch = dispatcher(app)
    for job_id in redispatch:
        dispatch(job_id, 0)
    if redispatch or failed:
        logger.info("reaped jobs", extra={"redispatched": len(redispatch), "failed": len(failed)})
    return redispatch, failed
