"""Service mode: PostgreSQL state, jobs, duplicates, failures and access (ADR-0029).

Needs PostgreSQL (``make services-up``); skipped otherwise. Jobs run inline (``jobs_eager``)
or are driven step by step with a recording dispatcher, with fake OCR and layout models.
The real Celery worker, and killing it mid-job, are in ``test_worker.py``.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from newgen_ocr.api.app import create_app
from newgen_ocr.audit import AuditEvent
from newgen_ocr.auth.tokens import Role
from newgen_ocr.config import Settings
from newgen_ocr.db.models import AuditEventRow, Base, Job, ReviewItemRow
from newgen_ocr.jobs.runner import JobRunner
from newgen_ocr.jobs.service import JobService
from newgen_ocr.services import Services
from tests.conftest import MODELS_DIR, MODELS_LOCK
from tests.fixtures.layout import FakeLayoutModels
from tests.fixtures.ocr import FakeEngine
from tests.fixtures.services import PLACEHOLDER_URLS

DOCS = "/api/v1/documents"
JOBS = "/api/v1/jobs"
PIPELINE = ["ingest", "preprocessing", "ocr", "layout", "extraction"]
DOCUMENT_TEXT = "Consignee Rohan Mehta 9876543210"  # document content: must never be logged


def png(seed: int = 0) -> bytes:
    page = np.full((600, 500), 255, np.uint8)
    page[100 + seed % 50 : 120 + seed % 50, 50:300] = 0  # distinct files per seed
    buf = io.BytesIO()
    Image.fromarray(page).save(buf, "PNG")
    return buf.getvalue()


def make_settings(database_url: str, data_dir: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "data_dir": data_dir,
        "models_dir": MODELS_DIR,
        "models_lock": MODELS_LOCK,
        "database_url": database_url,
        "broker_url": PLACEHOLDER_URLS["broker_url"],
        "jobs_eager": True,
        "job_lease_s": 30,
        "_env_file": None,
    }
    return Settings(**{**values, **overrides})


def fake_models(app: FastAPI) -> None:
    services: Services = app.state.services
    services.ocr.engines = {
        "paddle": FakeEngine("paddle", DOCUMENT_TEXT),
        "tesseract": FakeEngine("tesseract", DOCUMENT_TEXT),
    }
    services.ocr.detector = None
    services.layout.models = FakeLayoutModels()


class Recorder:
    """A dispatcher that records job ids instead of publishing them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int]] = []

    def __call__(self, job_id: str, delay_s: int = 0) -> None:
        self.sent.append((job_id, delay_s))


def queued_mode(app: FastAPI) -> Recorder:
    """Switch the app to queue jobs (as with workers) and return the recorder."""
    recorder = Recorder()
    app.state.jobs = JobService(app.state.services, dispatch=recorder)
    return recorder


def run(app: FastAPI, job_id: str, worker: str = "w1") -> Any:
    return JobRunner(app.state.services, Recorder(), worker=worker).run(job_id)


@pytest.fixture
def settings_db(database_url: str, tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    return make_settings(database_url, data)


@pytest.fixture
def app(settings_db: Settings) -> FastAPI:
    app = create_app(settings_db)
    fake_models(app)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def token(app: FastAPI, user: str, role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.tokens.create(user, role)}"}


def upload(
    client: TestClient, headers: dict[str, str], seed: int = 0, **form: Any
) -> dict[str, Any]:
    extra = {k: v for k, v in form.items() if k != "key"}
    if "key" in form:
        headers = {**headers, "Idempotency-Key": form["key"]}
    response = client.post(
        DOCS, files={"file": (f"p{seed}.png", png(seed), "image/png")}, data=extra, headers=headers
    )
    assert response.status_code == 202, response.text
    assert response.headers["Location"] == f"{JOBS}/{response.json()['job']['id']}"
    body: dict[str, Any] = response.json()
    return body


def db_session(settings: Settings) -> Session:
    assert settings.database_url is not None
    engine = create_engine(settings.database_url.get_secret_value())
    return Session(engine)


# ------------------------------------------------------------------ schema


def test_migration_matches_models(settings_db: Settings) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    assert settings_db.database_url is not None
    engine = create_engine(settings_db.database_url.get_secret_value())
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert diff == []


def test_history_and_audit_tables_are_append_only(app: FastAPI, settings_db: Settings) -> None:
    app.state.audit.record(AuditEvent.AUTH_FAILED, reason="x")
    with db_session(settings_db) as session, pytest.raises(Exception, match="append-only"):
        session.execute(update(AuditEventRow).values(actor="someone-else"))
        session.flush()


# ------------------------------------------------------------------ end to end


def test_upload_runs_the_whole_pipeline(
    app: FastAPI, client: TestClient, settings_db: Settings
) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    submitted = upload(client, alice, document_type="auto")
    job = submitted["job"]
    assert submitted["duplicate"] is False
    assert (job["status"], job["completed_stages"], job["attempts"]) == ("succeeded", PIPELINE, 1)
    doc = job["document_id"]
    status = client.get(f"{DOCS}/{doc}/status", headers=alice).json()
    assert (status["status"], status["page_count"], status["submitted_by"]) == (
        "ready",
        1,
        "alice",
    )
    assert status["latest_job"]["id"] == job["id"]
    # Results through the usual endpoints, from the shared volume.
    assert client.get(f"{DOCS}/{doc}", headers=alice).json()["submitted_by"] == "alice"
    assert client.get(f"{DOCS}/{doc}/ocr/auto", headers=alice).status_code == 200
    assert client.get(f"{DOCS}/{doc}/extraction", headers=alice).json() == ["auto"]
    events = [e["event"] for e in client.get(f"{JOBS}/{job['id']}/events", headers=alice).json()]
    assert events[:2] == ["submitted", "started"] and events[-1] == "succeeded"
    assert events.count("stage_completed") == len(PIPELINE)
    with db_session(settings_db) as session:
        # The result and its review item are in PostgreSQL; audit events name the submitter.
        assert session.scalar(select(ReviewItemRow.document_id)) == doc
        actors = set(
            session.scalars(select(AuditEventRow.actor).where(AuditEventRow.document_id == doc))
        )
    assert actors == {"alice"}


def test_stage_jobs_rerun_part_of_the_pipeline(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    doc = upload(client, alice)["job"]["document_id"]
    response = client.post(f"{DOCS}/{doc}/layout", json={"ocr_run": "auto"}, headers=alice)
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert (job["kind"], job["stages"], job["status"]) == (
        "layout",
        ["layout", "extraction"],
        "succeeded",
    )
    generic = client.post(
        f"{DOCS}/{doc}/jobs", json={"kind": "ocr", "params": {"engine": "paddle"}}, headers=alice
    )
    assert generic.json()["job"]["stages"] == ["ocr", "layout", "extraction"]
    assert {"auto", "paddle"} <= set(client.get(f"{DOCS}/{doc}/ocr", headers=alice).json())
    unknown = client.post(
        f"{DOCS}/{doc}/jobs",
        json={"kind": "extraction", "params": {"document_type": "nope"}},
        headers=alice,
    )
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (422, "UNKNOWN_DOCUMENT_TYPE")


# ------------------------------------------------------------------ duplicates


def test_duplicate_submissions_return_the_original_job(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    bob = token(app, "bob", Role.SUBMITTER)
    first = upload(client, alice, seed=1)
    again = upload(client, alice, seed=1)
    assert again["duplicate"] and again["job"]["id"] == first["job"]["id"]
    # Another user's identical file is their own document.
    assert upload(client, bob, seed=1)["job"]["document_id"] != first["job"]["document_id"]
    forced = upload(client, alice, seed=1, force="true")
    assert not forced["duplicate"] and forced["job"]["document_id"] != first["job"]["document_id"]

    keyed = upload(client, alice, seed=2, key="order-42")
    replay = upload(client, alice, seed=2, key="order-42")
    assert replay["duplicate"] and replay["job"]["id"] == keyed["job"]["id"]
    conflict = client.post(
        DOCS,
        files={"file": ("x.png", png(3), "image/png")},
        headers={**alice, "Idempotency-Key": "order-42"},
    )
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "IDEMPOTENCY_CONFLICT")
    # Nothing is left in quarantine by refused or duplicate uploads.
    assert list((app.state.settings.data_dir / "quarantine").iterdir()) == []


def test_one_active_job_per_document(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    doc = upload(client, alice)["job"]["document_id"]
    recorder = queued_mode(app)
    first = client.post(f"{DOCS}/{doc}/jobs", json={"kind": "layout"}, headers=alice)
    assert first.status_code == 202 and first.json()["job"]["status"] == "queued"
    second = client.post(f"{DOCS}/{doc}/jobs", json={"kind": "extraction"}, headers=alice)
    assert (second.status_code, second.json()["error"]["code"]) == (409, "JOB_ALREADY_ACTIVE")
    assert [j for j, _ in recorder.sent] == [first.json()["job"]["id"]]
    # Delivering the same message twice runs the job once.
    job_id = first.json()["job"]["id"]
    assert run(app, job_id) == "succeeded"
    assert run(app, job_id) is None


def test_stage_needs_an_ingested_document(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    queued_mode(app)
    job = upload(client, alice)["job"]
    assert job["status"] == "queued"
    doc = job["document_id"]
    early = client.post(f"{DOCS}/{doc}/jobs", json={"kind": "layout"}, headers=alice)
    assert (early.status_code, early.json()["error"]["code"]) == (409, "DOCUMENT_NOT_READY")
    manifest = client.get(f"{DOCS}/{doc}", headers=alice)
    assert (manifest.status_code, manifest.json()["error"]["code"]) == (409, "DOCUMENT_NOT_READY")


# ------------------------------------------------------------------ admission


def test_admission_limits(database_url: str, tmp_path: Path) -> None:
    settings = make_settings(database_url, tmp_path, max_active_jobs_per_user=1, max_queued_jobs=2)
    app = create_app(settings)
    fake_models(app)
    queued_mode(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        alice, bob, carol = (token(app, u, Role.SUBMITTER) for u in ("alice", "bob", "carol"))
        upload(client, alice, seed=1)
        too_many = client.post(DOCS, files={"file": ("a.png", png(2), "image/png")}, headers=alice)
        assert (too_many.status_code, too_many.json()["error"]["code"]) == (429, "TOO_MANY_JOBS")
        upload(client, bob, seed=3)
        full = client.post(DOCS, files={"file": ("c.png", png(4), "image/png")}, headers=carol)
        assert (full.status_code, full.json()["error"]["code"]) == (503, "QUEUE_FULL")
    assert len(list((tmp_path / "quarantine").iterdir())) == 2  # only the accepted uploads


# ------------------------------------------------------------------ failures and recovery


def test_transient_failure_retries_then_fails_and_can_be_retried(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    layout = app.state.services.layout
    real = layout.analyze
    monkeypatch.setattr(layout, "analyze", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    job = upload(client, alice)["job"]
    assert (job["status"], job["error_code"], job["attempts"]) == ("failed", "INTERNAL_ERROR", 3)
    assert job["completed_stages"] == ["ingest", "preprocessing", "ocr"]
    assert "disk" not in (job["error_message"] or "")  # exception text is never stored
    events = [e["event"] for e in client.get(f"{JOBS}/{job['id']}/events", headers=alice).json()]
    assert events.count("retry_scheduled") == 2 and events[-1] == "failed"
    failed = client.get(JOBS, params={"status": "failed"}, headers=alice).json()
    assert [j["id"] for j in failed["items"]] == [job["id"]]

    monkeypatch.setattr(layout, "analyze", real)
    engine = app.state.services.ocr.engines["paddle"]
    calls = len(engine.calls)
    retried = client.post(f"{JOBS}/{job['id']}/retry", headers=alice).json()
    assert (retried["status"], retried["completed_stages"]) == ("succeeded", PIPELINE)
    assert len(engine.calls) == calls  # resumed after OCR: completed stages are not re-run


def test_rejected_upload_fails_at_once(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200  # PNG signature, undecodable content
    response = client.post(DOCS, files={"file": ("b.png", broken, "image/png")}, headers=alice)
    job = response.json()["job"]
    assert (job["status"], job["attempts"], job["error_code"]) == ("failed", 1, "CORRUPT_DOCUMENT")
    status = client.get(f"{DOCS}/{job['document_id']}/status", headers=alice).json()
    assert (status["status"], status["error_code"]) == ("rejected", "CORRUPT_DOCUMENT")
    retry = client.post(f"{JOBS}/{job['id']}/retry", headers=alice)
    assert (retry.status_code, retry.json()["error"]["code"]) == (409, "JOB_CONFLICT")
    # A rejected file is not served as a duplicate: uploading it again is a new attempt.
    again = client.post(DOCS, files={"file": ("b.png", broken, "image/png")}, headers=alice)
    assert again.json()["job"]["document_id"] != job["document_id"]


def test_cancel_queued_and_running_jobs(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    queued_mode(app)
    job = upload(client, alice, seed=1)["job"]
    cancelled = client.post(f"{JOBS}/{job['id']}/cancel", headers=alice).json()
    assert cancelled["status"] == "cancelled"
    assert run(app, job["id"]) is None  # a late message does nothing
    again = client.post(f"{JOBS}/{job['id']}/cancel", headers=alice)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "JOB_CONFLICT")

    # Running: stops before its next stage; finished stages keep their results.
    job = upload(client, alice, seed=2)["job"]
    services: Services = app.state.services
    original = services.ocr.run

    def ocr_then_cancel(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        client.post(f"{JOBS}/{job['id']}/cancel", headers=alice)
        return result

    services.ocr.run = ocr_then_cancel  # type: ignore[method-assign]
    assert run(app, job["id"]) == "cancelled"
    final = client.get(f"{JOBS}/{job['id']}", headers=alice).json()
    assert final["completed_stages"] == ["ingest", "preprocessing", "ocr"]
    services.ocr.run = original  # type: ignore[method-assign]
    resumed = client.post(f"{JOBS}/{job['id']}/retry", headers=alice).json()
    assert resumed["status"] == "queued"
    assert run(app, job["id"]) == "succeeded"


def _expire(settings: Settings, job_id: str) -> None:
    with db_session(settings) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
        )


def test_worker_crash_mid_ingest_is_recovered(
    app: FastAPI, client: TestClient, settings_db: Settings
) -> None:
    """A worker dies after starting a job: the lease expires, the reaper requeues it, and
    another worker finishes it from clean state (no half-written document)."""
    alice = token(app, "alice", Role.SUBMITTER)
    queued_mode(app)
    job = upload(client, alice)["job"]
    repo = app.state.services.jobs
    assert repo.claim(job["id"], "dead-worker", 30) is not None
    assert repo.stage_started(job["id"], "ingest", "dead-worker") == "ok"
    # It had half-staged the document and moved the original out of quarantine.
    store = app.state.services.store
    store.stage(job["document_id"])
    original_dir = settings_db.data_dir / "originals" / job["document_id"]
    original_dir.mkdir(parents=True)
    store.quarantine_path(job["document_id"]).rename(original_dir / "original.png")

    assert run(app, job["id"], "w2") is None  # still leased: nobody else may take it
    _expire(settings_db, job["id"])
    redispatch, failed = repo.reap()
    assert (redispatch, failed) == ([job["id"]], [])
    assert run(app, job["id"], "w2") == "succeeded"
    # The dead worker, if it came back, could not write anything.
    assert repo.stage_completed(job["id"], "ingest", "dead-worker") is False
    events = [e.event for e in repo.events(job["id"])]
    assert "lease_expired" in events and events[-1] == "succeeded"
    assert client.get(f"{JOBS}/{job['id']}", headers=alice).json()["attempts"] == 2


def test_worker_lost_on_last_attempt_fails_observably(
    app: FastAPI, client: TestClient, settings_db: Settings
) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    queued_mode(app)
    job = upload(client, alice)["job"]
    repo = app.state.services.jobs
    for attempt in range(3):
        assert repo.claim(job["id"], f"dead-{attempt}", 30) is not None
        _expire(settings_db, job["id"])
        redispatch, failed = repo.reap()
    assert (redispatch, failed) == ([], [job["id"]])
    lost = client.get(f"{JOBS}/{job['id']}", headers=alice).json()
    assert (lost["status"], lost["error_code"]) == ("failed", "WORKER_LOST")
    assert client.post(f"{JOBS}/{job['id']}/retry", headers=alice).json()["status"] == "queued"
    assert run(app, job["id"]) == "succeeded"


def test_lost_messages_are_dispatched_again(
    app: FastAPI, client: TestClient, settings_db: Settings
) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    queued_mode(app)
    job = upload(client, alice)["job"]
    repo = app.state.services.jobs
    assert repo.reap() == ([], [])  # recent: its message may still be on its way
    with db_session(settings_db) as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == job["id"])
            .values(updated_at=datetime.now(tz=UTC) - timedelta(minutes=5))
        )
    assert repo.reap(include_stale=False) == ([], [])  # the broker still holds messages
    assert repo.reap() == ([job["id"]], [])
    assert repo.reap() == ([], [])  # not again until it is stale again


# ------------------------------------------------------------------ access


def test_access_control_and_pagination(app: FastAPI, client: TestClient) -> None:
    alice = token(app, "alice", Role.SUBMITTER)
    bob = token(app, "bob", Role.SUBMITTER)
    viewer = token(app, "vic", Role.VIEWER)
    admin = token(app, "root", Role.ADMIN)
    queued_mode(app)
    jobs = [upload(client, alice, seed=i)["job"] for i in range(3)]
    upload(client, bob, seed=9)
    doc = jobs[0]["document_id"]

    assert client.get(DOCS).status_code == 401
    assert client.get(JOBS).status_code == 401
    page = client.get(DOCS, params={"limit": 2, "offset": 0}, headers=alice).json()
    assert (page["total"], len(page["items"]), page["limit"]) == (3, 2, 2)
    rest = client.get(DOCS, params={"limit": 2, "offset": 2}, headers=alice).json()
    ids = {d["id"] for d in page["items"] + rest["items"]}
    assert ids == {j["document_id"] for j in jobs}
    assert client.get(DOCS, headers=viewer).json()["total"] == 4
    assert client.get(JOBS, headers=bob).json()["total"] == 1
    assert client.get(DOCS, params={"limit": 0}, headers=alice).status_code == 422

    # Someone else's document or job is not found (its existence is not revealed).
    for path in (f"{DOCS}/{doc}/status", f"{DOCS}/{doc}/ocr", f"{JOBS}/{jobs[0]['id']}"):
        hidden = client.get(path, headers=bob)
        assert hidden.status_code == 404, path
    assert client.post(f"{JOBS}/{jobs[0]['id']}/cancel", headers=bob).status_code == 404
    # A viewer sees it but cannot control it; an admin can.
    assert client.get(f"{JOBS}/{jobs[0]['id']}", headers=viewer).status_code == 200
    denied = client.post(f"{JOBS}/{jobs[0]['id']}/cancel", headers=viewer)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "FORBIDDEN")
    assert client.post(f"{JOBS}/{jobs[0]['id']}/cancel", headers=admin).status_code == 200
    # Submitters have no access to the review queue.
    assert client.get("/api/v1/review/items", headers=alice).status_code == 403


# ------------------------------------------------------------------ probes and logs


def test_readiness_checks_the_database(client: TestClient) -> None:
    body = client.get("/health/ready").json()
    assert body["checks"]["database"] and body["checks"]["database_schema_current"]
    assert body["status"] == "ready"


def test_no_credentials_or_content_in_logs(
    app: FastAPI, client: TestClient, settings_db: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    alice = token(app, "alice", Role.SUBMITTER)
    job = upload(client, alice)["job"]
    client.get(f"{JOBS}/{'0' * 32}", headers=alice)
    client.get(DOCS, headers={"Authorization": "Bearer ngocr_0123456789ab_" + "x" * 43})
    assert job["status"] == "succeeded"
    assert settings_db.database_url is not None
    password = make_url(settings_db.database_url.get_secret_value()).password
    assert password
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "job succeeded" in logged
    for secret in (password, DOCUMENT_TEXT, alice["Authorization"].split()[1], "x" * 43):
        assert secret not in logged
