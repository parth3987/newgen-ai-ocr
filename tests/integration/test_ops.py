"""Operations: restore verification and retention (ADR-0031). Needs PostgreSQL."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from newgen_ocr.api.app import create_app
from newgen_ocr.auth.tokens import Role
from newgen_ocr.db.models import AuditEventRow, Document, JobEvent, ReviewItemRow
from newgen_ocr.ops import retention, verify
from newgen_ocr.services import prepare_temp_dir
from tests.conftest import ROOT
from tests.integration.test_jobs import fake_models, make_settings, token, upload


@pytest.fixture
def app(database_url: str, tmp_path: Path) -> FastAPI:
    data = tmp_path / "data"
    data.mkdir()
    app = create_app(make_settings(database_url, data, retention_document_days=30))
    fake_models(app)
    return app


def _age(app: FastAPI, document_id: str, days: int) -> None:
    with app.state.db.session() as session:
        session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(created_at=datetime.now(tz=UTC) - timedelta(days=days))
        )


def test_verify_reports_agreement_and_damage(app: FastAPI) -> None:
    with TestClient(app) as client:
        alice = token(app, "alice", Role.SUBMITTER)
        doc = upload(client, alice, seed=1)["job"]["document_id"]
        upload(client, alice, seed=2)
    services = app.state.services
    report = verify(services)
    assert report["ok"], report["problems"]
    assert report["counts"]["documents"] == 2 and report["counts"]["extraction_results"] == 2
    assert report["review_history"]["valid"] and report["schema_current"]
    assert verify(services)["fingerprint"] == report["fingerprint"]  # stable
    # A lost file (e.g. an incomplete restore) is reported, not ignored.
    (services.store.derived_dir / doc / "manifest.json").unlink()
    damaged = verify(services)
    assert not damaged["ok"] and any(doc in p for p in damaged["problems"])


def test_retention_keeps_open_work_and_deletes_the_rest(app: FastAPI) -> None:
    with TestClient(app) as client:
        alice = token(app, "alice", Role.SUBMITTER)
        old_done = upload(client, alice, seed=1)["job"]["document_id"]
        old_open = upload(client, alice, seed=2)["job"]["document_id"]
        recent = upload(client, alice, seed=3)["job"]["document_id"]
    services, settings = app.state.services, app.state.settings
    with services.db.session() as session:
        session.execute(
            update(ReviewItemRow)
            .where(ReviewItemRow.document_id == old_done)
            .values(status="approved")
        )
        session.execute(update(JobEvent).values(ts=datetime.now(tz=UTC) - timedelta(days=200)))
    for doc in (old_done, old_open):
        _age(app, doc, 45)
    plan = retention(services, settings, apply=False)
    assert plan["documents"] == [old_done] and plan["kept_open"] == 1 and not plan["applied"]
    assert (services.store.derived_dir / old_done).is_dir()  # dry run changed nothing

    applied = retention(services, settings, apply=True)
    assert applied["documents"] == [old_done]
    assert not (services.store.derived_dir / old_done).exists()
    assert not (services.store.originals_dir / old_done).exists()
    with services.db.session() as session:
        remaining = set(session.scalars(select(Document.id)))
        deleted = session.scalars(
            select(AuditEventRow.document_id).where(AuditEventRow.event == "document.deleted")
        ).all()
    assert remaining == {old_open, recent} and deleted == [old_done]
    assert verify(services)["ok"]


def test_temporary_files_stay_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data" / "tmp"
    monkeypatch.setenv("TMPDIR", str(target))
    prepare_temp_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_only_locked_models_pass_verification(tmp_path: Path) -> None:
    models = tmp_path / "models" / "paddle" / "m"
    models.mkdir(parents=True)
    (models / "a.bin").write_bytes(b"weights")
    import hashlib

    lock = {
        "models": [
            {
                "id": "paddle/m",
                "dir": "paddle/m",
                "files": {"a.bin": hashlib.sha256(b"weights").hexdigest()},
            }
        ]
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))

    def check() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - our own script, fixed arguments
            [
                sys.executable,
                str(ROOT / "scripts" / "fetch_models.py"),
                "--verify",
                "--lock",
                str(lock_path),
                "--models-dir",
                str(tmp_path / "models"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    assert check().returncode == 0
    (models / "extra.bin").write_bytes(b"not approved")
    unlisted = check()
    assert unlisted.returncode == 1 and "UNLISTED" in unlisted.stdout
    os.remove(models / "extra.bin")
    (models / "a.bin").write_bytes(b"tampered")
    assert "MISMATCH" in check().stdout
