"""Background worker: ``python -m newgen_ocr.worker`` (ADR-0029).

Runs Celery with ``worker_concurrency`` processes on the ``jobs`` queue, plus a reaper
thread in the main process that every minute requeues jobs whose worker died and queued
jobs whose message was lost. Every worker may reap: the database serialises it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery.signals import worker_ready, worker_shutdown

from newgen_ocr.config import get_settings
from newgen_ocr.imaging.runtime import check_opencv_build, configure_opencv
from newgen_ocr.jobs.celery_app import QUEUE, make_celery, reap_once
from newgen_ocr.logging_config import configure_logging
from newgen_ocr.services import build_services

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    check_opencv_build(settings.environment)
    configure_opencv(settings.opencv_threads)
    app = make_celery(settings)
    stop = threading.Event()

    def reaper() -> None:
        # Its own services and connections: built after the pool forked, never shared.
        svc = build_services(settings, pool_size=1)
        while not stop.wait(settings.job_reap_interval_s):
            try:
                reap_once(svc, app)
            except Exception:  # keep reaping after a database or broker error
                logger.exception("reaper pass failed")

    @worker_ready.connect  # type: ignore[untyped-decorator]
    def _start(**_: Any) -> None:
        threading.Thread(target=reaper, name="reaper", daemon=True).start()

    @worker_shutdown.connect  # type: ignore[untyped-decorator]
    def _stop(**_: Any) -> None:
        stop.set()

    logger.info("worker starting", extra={"concurrency": settings.worker_concurrency})
    app.worker_main(
        [
            "worker",
            f"--queues={QUEUE}",
            f"--concurrency={settings.worker_concurrency}",
            f"--loglevel={settings.log_level}",
            "--pool=prefork",
            # No worker-to-worker chatter: nothing here needs it.
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ]
    )


if __name__ == "__main__":
    main()
