"""The real worker: Celery on Valkey, PostgreSQL state, real OCR models (ADR-0029).

Needs ``make services-up`` (and the models for the kill test); skipped otherwise. Starts ``python -m
newgen_ocr.worker`` as a subprocess (``NGOCR_TEST_WORKER_CMD`` overrides the command), kills
it in the middle of a job, starts another and checks the job finishes exactly once.
"""

from __future__ import annotations

import io
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from newgen_ocr.api.app import create_app
from newgen_ocr.auth.tokens import Role
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.extraction_corpus import gst_invoice
from tests.conftest import MODELS_DIR, MODELS_LOCK
from tests.fixtures.services import PLACEHOLDER_URLS, TEST_BROKER_URL

DOCS = "/api/v1/documents"
LEASE_S = 10


def _environment(database_url: str, data_dir: Path) -> dict[str, str]:
    assert TEST_BROKER_URL is not None
    return {
        "NGOCR_ENVIRONMENT": "test",
        "NGOCR_DATA_DIR": str(data_dir),
        "NGOCR_MODELS_DIR": str(MODELS_DIR),
        "NGOCR_MODELS_LOCK": str(MODELS_LOCK),
        "NGOCR_DATABASE_URL": database_url,
        "NGOCR_BROKER_URL": TEST_BROKER_URL,
        "NGOCR_JOB_LEASE_S": str(LEASE_S),
        "NGOCR_JOB_REAP_INTERVAL_S": "2",
        "NGOCR_BROKER_VISIBILITY_TIMEOUT_S": "60",
        "NGOCR_LOG_LEVEL": "WARNING",
    }


@pytest.fixture
def environment(database_url: str, tmp_path: Path) -> Iterator[dict[str, str]]:
    if not TEST_BROKER_URL:
        pytest.skip("NGOCR_TEST_BROKER_URL not set (make services-up)")
    redis.Redis.from_url(TEST_BROKER_URL).flushdb()  # db 15: tests only
    data = tmp_path / "data"
    data.mkdir()
    yield _environment(database_url, data)
    redis.Redis.from_url(TEST_BROKER_URL).flushdb()


def start_worker(environment: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    command = shlex.split(
        os.environ.get("NGOCR_TEST_WORKER_CMD", f"{sys.executable} -m newgen_ocr.worker")
    )
    with log.open("ab") as out:
        return subprocess.Popen(  # noqa: S603 - our own worker, fixed arguments
            command,
            env={**os.environ, **environment},
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group: kill the pool with it
        )


def kill(worker: subprocess.Popen[bytes]) -> None:
    """As a crash or power loss would: no warm shutdown, no acknowledgement."""
    os.killpg(worker.pid, signal.SIGKILL)
    worker.wait(timeout=30)


def wait_for(check: Any, timeout_s: float, what: str) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.mark.models
@pytest.mark.tesseract
def test_jobs_survive_a_killed_worker(environment: dict[str, str], tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        environment="test",
        data_dir=Path(environment["NGOCR_DATA_DIR"]),
        models_dir=MODELS_DIR,
        models_lock=MODELS_LOCK,
        database_url=environment["NGOCR_DATABASE_URL"],
        broker_url=environment["NGOCR_BROKER_URL"],
        _env_file=None,
    )
    app = create_app(settings)
    log = tmp_path / "worker.log"
    image, _ = gst_invoice("intra", 200)
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, "PNG")
    workers: list[subprocess.Popen[bytes]] = []
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            headers = {
                "Authorization": f"Bearer {app.state.tokens.create('alice', Role.SUBMITTER)}"
            }
            ready = client.get("/health/ready").json()
            assert ready["checks"]["broker"] and ready["checks"]["database_schema_current"]
            response = client.post(
                DOCS,
                files={"file": ("inv.png", buf.getvalue(), "image/png")},
                data={"languages": "en"},
                headers=headers,
            )
            assert response.status_code == 202, response.text
            job_url = response.headers["Location"]

            def job() -> dict[str, Any]:
                body: dict[str, Any] = client.get(job_url, headers=headers).json()
                return body

            workers.append(start_worker(environment, log))
            wait_for(lambda: job()["current_stage"] == "ocr", 120, "the OCR stage")
            kill(workers.pop())
            assert job()["status"] == "running"  # nobody has noticed yet

            workers.append(start_worker(environment, log))
            final = wait_for(
                lambda: (j := job())["status"] in ("succeeded", "failed") and j, 300, "the job"
            )
            assert final["status"] == "succeeded", (final, log.read_text()[-3000:])
            assert final["attempts"] == 2
            assert final["completed_stages"] == [
                "ingest",
                "preprocessing",
                "ocr",
                "layout",
                "extraction",
            ]
            events = [e["event"] for e in client.get(f"{job_url}/events", headers=headers).json()]
            assert events.count("started") == 2 and "lease_expired" in events
            assert events.count("stage_completed") == 5  # nothing ran twice to completion
            status = client.get(f"{DOCS}/{final['document_id']}/status", headers=headers).json()
            assert status["status"] == "ready"
            item = client.get(
                f"/api/v1/review/items/{final['document_id']}/auto",
                headers={"Authorization": f"Bearer {app.state.tokens.create('vic', Role.VIEWER)}"},
            )
            assert item.status_code == 200
    finally:
        for worker in workers:
            kill(worker)


def test_reaper_sees_whether_the_broker_holds_messages(environment: dict[str, str]) -> None:
    """Lost messages are re-sent only when the queue is empty: an empty queue must read as
    0 (Valkey deletes empty lists), and an unreachable broker as unknown."""
    from newgen_ocr.jobs.celery_app import dispatcher, make_celery, pending_messages

    settings = Settings(  # type: ignore[call-arg]
        environment="test",
        database_url=environment["NGOCR_DATABASE_URL"],
        broker_url=environment["NGOCR_BROKER_URL"],
        _env_file=None,
    )
    app = make_celery(settings)
    assert pending_messages(app) == 0
    dispatcher(app)("0" * 32, 0)
    assert pending_messages(app) == 1
    unreachable = make_celery(
        settings.model_copy(update={"broker_url": SecretStr(PLACEHOLDER_URLS["broker_url"])})
    )
    assert pending_messages(unreachable) is None


def test_reaper_resends_a_lost_message(environment: dict[str, str], tmp_path: Path) -> None:
    """A job whose message never reached Valkey (or was lost) is published again."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from newgen_ocr.db.models import Job
    from newgen_ocr.jobs.celery_app import make_celery, pending_messages, reap_once
    from newgen_ocr.jobs.models import JobKind, JobParams
    from newgen_ocr.services import build_services

    settings = Settings(  # type: ignore[call-arg]
        environment="test",
        data_dir=Path(environment["NGOCR_DATA_DIR"]),
        database_url=environment["NGOCR_DATABASE_URL"],
        broker_url=environment["NGOCR_BROKER_URL"],
        _env_file=None,
    )
    services = build_services(settings, pool_size=1)
    assert services.jobs is not None and services.db is not None
    job, _ = services.jobs.submit(
        kind=JobKind.PIPELINE,
        params=JobParams(),
        submitted_by="alice",
        idempotency_key=None,
        fingerprint="0" * 64,
        document={"sha256": "0" * 64, "size_bytes": 1, "format": "png"},
        document_id="a" * 32,
        max_queued=10,
        max_active_per_user=10,
    )
    with services.db.session() as session:  # never dispatched, five minutes ago
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(updated_at=datetime.now(tz=UTC) - timedelta(minutes=5))
        )
    celery = make_celery(settings)
    assert pending_messages(celery) == 0
    assert reap_once(services, celery) == ([job.id], [])
    assert pending_messages(celery) == 1
    assert reap_once(services, celery) == ([], [])  # the queue holds it now
