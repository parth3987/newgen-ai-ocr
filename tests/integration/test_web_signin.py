"""Web sign-in, sessions, CSRF, administration, search and the web application (ADR-0030).

Service mode on a real PostgreSQL (``make services-up``); skipped otherwise. Access rules are
checked against the API directly: the web application only mirrors them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from newgen_ocr.api.app import create_app
from newgen_ocr.auth.tokens import Role
from newgen_ocr.config import Settings
from newgen_ocr.db.models import UserSession
from tests.integration.test_jobs import DOCS, fake_models, make_settings, png

PASSWORD = "correct horse battery"  # noqa: S105 - test account
LOGIN = "/api/v1/auth/login"


@pytest.fixture
def app(database_url: str, tmp_path: Path) -> FastAPI:
    data = tmp_path / "data"
    data.mkdir()
    app = create_app(make_settings(database_url, data))
    fake_models(app)
    for name, role in (
        ("alice", Role.SUBMITTER),
        ("bob", Role.SUBMITTER),
        ("vic", Role.VIEWER),
        ("rita", Role.REVIEWER),
        ("root", Role.ADMIN),
    ):
        app.state.users.create(name, role, PASSWORD)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def browser(app: FastAPI, user: str, password: str = PASSWORD) -> tuple[TestClient, dict[str, str]]:
    """A separate client (its own cookie jar) signed in as ``user``, and its CSRF header."""
    c = TestClient(app, raise_server_exceptions=False)
    response = c.post(LOGIN, json={"user": user, "password": password})
    assert response.status_code == 200, response.text
    return c, {"X-CSRF-Token": response.json()["csrf_token"]}


def _expire(app: FastAPI, **values: Any) -> None:
    with app.state.db.session() as session:
        session.execute(update(UserSession).values(**values))


# ------------------------------------------------------------------ sign-in


def test_login_sets_a_protected_session_cookie(app: FastAPI, client: TestClient) -> None:
    response = client.post(LOGIN, json={"user": "Alice ", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert (body["user"], body["role"]) == ("alice", "submitter") and len(body["csrf_token"]) == 64
    cookie = response.headers["set-cookie"].lower()
    assert "ngocr_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # test environment: plain http
    assert PASSWORD not in response.text
    session = client.get("/api/v1/auth/session").json()
    assert session["user"] == "alice" and session["csrf_token"] == body["csrf_token"]
    assert client.get(f"{DOCS}").status_code == 200


def test_secure_cookie_outside_development(database_url: str, tmp_path: Path) -> None:
    settings = make_settings(database_url, tmp_path, environment="staging", jobs_eager=True)
    app = create_app(settings)
    app.state.users.create("alice", Role.SUBMITTER, PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        cookie = c.post(LOGIN, json={"user": "alice", "password": PASSWORD}).headers["set-cookie"]
    assert "Secure" in cookie
    with pytest.raises(ValueError, match="Secure"):
        Settings(  # type: ignore[call-arg]
            environment="production",
            database_url=database_url,
            broker_url="redis://127.0.0.1:9/0",
            session_cookie_secure=False,
            _env_file=None,
        )


def test_refused_logins_look_the_same_and_lock_the_account(
    app: FastAPI, client: TestClient
) -> None:
    unknown = client.post(LOGIN, json={"user": "nobody", "password": PASSWORD})
    wrong = client.post(LOGIN, json={"user": "alice", "password": "wrong password!"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]
    assert "set-cookie" not in wrong.headers
    for _ in range(4):
        client.post(LOGIN, json={"user": "alice", "password": "wrong password!"})
    locked = client.post(LOGIN, json={"user": "alice", "password": PASSWORD})
    assert (locked.status_code, locked.json()["error"]["code"]) == (401, "LOGIN_FAILED")
    admin, csrf = browser(app, "root")
    users = {u["name"]: u for u in admin.get("/api/v1/admin/users").json()}
    assert users["alice"]["locked_until"] is not None
    # Unlocked by a new password from an admin.
    new = "another long password"
    assert (
        admin.patch("/api/v1/admin/users/alice", json={"password": new}, headers=csrf).status_code
        == 200
    )
    assert client.post(LOGIN, json={"user": "alice", "password": new}).status_code == 200
    audit = admin.get("/api/v1/admin/audit", params={"event": "auth."}).json()["items"]
    reasons = {e["fields"].get("reason") for e in audit if e["event"] == "auth.login_failed"}
    assert {"unknown", "wrong_password", "locked"} <= reasons
    assert PASSWORD not in json.dumps(audit) and new not in json.dumps(audit)


def test_csrf_token_is_required_for_changes(app: FastAPI) -> None:
    c, csrf = browser(app, "alice")
    files = {"file": ("a.png", png(1), "image/png")}
    refused = c.post(DOCS, files=files)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "CSRF_FAILED")
    forged = c.post(DOCS, files=files, headers={"X-CSRF-Token": "0" * 64})
    assert forged.status_code == 403
    accepted = c.post(DOCS, files=files, headers=csrf)
    assert accepted.status_code == 202, accepted.text
    # Reads need no token; bearer callers never need one.
    assert c.get(DOCS).status_code == 200
    token = app.state.tokens.create("bot", Role.SUBMITTER)
    with TestClient(app) as api:
        bearer = api.post(
            DOCS,
            files={"file": ("b.png", png(2), "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert bearer.status_code == 202


def test_sessions_end_on_logout_idle_timeout_and_lifetime(app: FastAPI) -> None:
    c, csrf = browser(app, "alice")
    assert c.post("/api/v1/auth/logout").status_code == 403  # logout needs the token too
    assert c.post("/api/v1/auth/logout", headers=csrf).status_code == 204
    assert c.get(DOCS).status_code == 401

    c, _ = browser(app, "alice")
    _expire(app, last_seen_at=datetime.now(tz=UTC) - timedelta(minutes=31))
    assert c.get(DOCS).status_code == 401

    c, _ = browser(app, "alice")
    _expire(app, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    assert c.get(DOCS).status_code == 401


def test_disabling_or_changing_a_user_applies_at_once(app: FastAPI) -> None:
    rita, _ = browser(app, "rita")
    assert rita.get("/api/v1/review/items").status_code == 200
    admin, csrf = browser(app, "root")
    assert (
        admin.patch(
            "/api/v1/admin/users/rita", json={"role": "submitter"}, headers=csrf
        ).status_code
        == 200
    )
    assert rita.get("/api/v1/review/items").status_code == 401  # signed out by the change
    rita, _ = browser(app, "rita")
    assert rita.get("/api/v1/review/items").status_code == 403  # now a submitter
    assert (
        admin.patch("/api/v1/admin/users/rita", json={"disabled": True}, headers=csrf).status_code
        == 200
    )
    assert rita.get(DOCS).status_code == 401
    disabled = rita.post(LOGIN, json={"user": "rita", "password": PASSWORD})
    assert disabled.status_code == 401
    me = admin.patch("/api/v1/admin/users/root", json={"disabled": True}, headers=csrf)
    assert (me.status_code, me.json()["error"]["code"]) == (403, "FORBIDDEN")


def test_password_change(app: FastAPI) -> None:
    c, csrf = browser(app, "alice")
    wrong = c.post(
        "/api/v1/auth/password",
        json={"current_password": "nope", "new_password": "a new long passphrase"},
        headers=csrf,
    )
    assert wrong.status_code == 401
    weak = c.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=csrf,
    )
    assert (weak.status_code, weak.json()["error"]["code"]) == (422, "WEAK_PASSWORD")
    ok = c.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "a new long passphrase"},
        headers=csrf,
    )
    assert ok.status_code == 204
    assert c.get(DOCS).status_code == 401
    assert browser(app, "alice", "a new long passphrase")[0].get(DOCS).status_code == 200


# ------------------------------------------------------------------ access rules


def test_access_rules_are_enforced_by_the_api(app: FastAPI) -> None:
    """Whatever the web application shows, the API refuses what a role may not do."""
    alice, alice_csrf = browser(app, "alice")
    bob, _ = browser(app, "bob")
    vic, vic_csrf = browser(app, "vic")
    submitted = alice.post(DOCS, files={"file": ("a.png", png(3), "image/png")}, headers=alice_csrf)
    doc, job = submitted.json()["job"]["document_id"], submitted.json()["job"]["id"]

    for path in (f"{DOCS}/{doc}/status", f"{DOCS}/{doc}/pages/1/image", f"/api/v1/jobs/{job}"):
        assert bob.get(path).status_code == 404, path
    assert bob.get("/api/v1/extractions").json()["total"] == 0
    assert alice.get("/api/v1/extractions").json()["total"] == 1
    for path in ("/api/v1/review/items", f"/api/v1/review/items/{doc}/auto"):
        assert alice.get(path).status_code == 403, path
    for path in ("/api/v1/admin/overview", "/api/v1/admin/audit", "/api/v1/admin/users"):
        assert vic.get(path).status_code == 403, path
    made = vic.post(
        "/api/v1/admin/users",
        json={"name": "eve", "role": "admin", "password": "a long password here"},
        headers=vic_csrf,
    )
    assert made.status_code == 403
    assert vic.post(f"/api/v1/jobs/{job}/retry", headers=vic_csrf).status_code == 403


# ------------------------------------------------------------------ search and admin


def test_search_documents_and_extractions(app: FastAPI) -> None:
    alice, csrf = browser(app, "alice")
    for seed, name in ((1, "invoice-march.png"), (2, "receipt.png")):
        response = alice.post(DOCS, files={"file": (name, png(seed), "image/png")}, headers=csrf)
        assert response.status_code == 202
    found = alice.get(DOCS, params={"q": "MARCH"}).json()
    assert [d["filename"] for d in found["items"]] == ["invoice-march.png"]
    assert alice.get(DOCS, params={"q": "100%"}).json()["total"] == 0  # literal, not a pattern
    today = datetime.now(tz=UTC).date()
    assert alice.get(DOCS, params={"created_from": f"{today}T00:00:00"}).json()["total"] == 2
    assert alice.get(DOCS, params={"created_to": f"{today}T00:00:00Z"}).json()["total"] == 0
    results = alice.get("/api/v1/extractions", params={"q": "receipt"}).json()
    assert [r["filename"] for r in results["items"]] == ["receipt.png"]
    assert results["items"][0]["review_status"] == "pending"
    assert alice.get("/api/v1/extractions", params={"status": "unclassified"}).json()["total"] == 2


def test_admin_overview_audit_and_users(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    admin, csrf = browser(app, "root")
    alice, alice_csrf = browser(app, "alice")
    alice.post(DOCS, files={"file": ("a.png", png(4), "image/png")}, headers=alice_csrf)
    overview = admin.get("/api/v1/admin/overview").json()
    assert overview["jobs"]["succeeded"] == 1 and overview["documents"] == {"ready": 1}
    assert overview["review"] == {"pending": 1} and overview["active_sessions"] == 2
    audit = admin.get("/api/v1/admin/audit", params={"actor": "alice", "limit": 5}).json()
    assert audit["total"] >= 3 and all(e["actor"] == "alice" for e in audit["items"])
    events = {
        e["event"] for e in admin.get("/api/v1/admin/audit", params={"limit": 200}).json()["items"]
    }
    assert {"auth.login", "document.accepted", "review.queued"} <= events

    new = admin.post(
        "/api/v1/admin/users",
        json={"name": "nina", "role": "approver", "password": "nina's long password"},
        headers=csrf,
    )
    assert (new.status_code, new.json()["error"]["code"]) == (422, "WEAK_PASSWORD")  # has the name
    new = admin.post(
        "/api/v1/admin/users",
        json={"name": "nina", "role": "approver", "password": "a long password here"},
        headers=csrf,
    )
    assert new.status_code == 201 and new.json()["role"] == "approver"
    again = admin.post(
        "/api/v1/admin/users",
        json={"name": "nina", "role": "viewer", "password": "a long password here"},
        headers=csrf,
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "USER_EXISTS")
    missing = admin.patch("/api/v1/admin/users/zed", json={"disabled": True}, headers=csrf)
    assert missing.status_code == 404
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert PASSWORD not in logged and "a long password here" not in logged


# ------------------------------------------------------------------ the web application


def test_web_application_is_served_with_a_strict_policy(client: TestClient) -> None:
    assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
    assert client.get("/review", follow_redirects=False).headers["location"] == "/app/review"
    shell = client.get("/app/")
    deep = client.get("/app/documents/0123")
    assert shell.status_code == deep.status_code
    csp = shell.headers["Content-Security-Policy"]
    assert (
        "default-src 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp
    )
    assert shell.headers["X-Frame-Options"] == "DENY"
    assert "https://" not in shell.text and "http://" not in shell.text
    if shell.status_code == 503:  # not built here: say so instead of a blank page
        assert "make frontend" in shell.text
        return
    assert shell.headers["Cache-Control"] == "no-store"
    script = shell.text.split('src="')[1].split('"')[0]
    asset = client.get(script)
    assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
    assert "immutable" in asset.headers["Cache-Control"]
    assert client.get("/app/assets/missing.js").status_code == 404
    # Encoded traversal never reaches a file: it is a client route, so the shell comes back.
    traversal = client.get("/app/assets/..%2F..%2Fapi%2Fauth.py")
    assert traversal.text == shell.text and "import" not in traversal.text


def test_sign_in_needs_service_mode(tmp_path: Path) -> None:
    settings = Settings(environment="test", data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        response = c.post(LOGIN, json={"user": "a", "password": "b"})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "SIGN_IN_UNAVAILABLE")
