"""Local bearer tokens: hashing, roles, expiry, revocation and the management CLI."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newgen_ocr.auth import __main__ as cli
from newgen_ocr.auth.tokens import AuthFailure, Principal, Role, TokenStore, token_id_of
from newgen_ocr.config import get_settings


def test_create_authenticate_and_revoke(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "auth" / "tokens.json")
    token = store.create("asha", Role.REVIEWER)
    principal = store.authenticate(token)
    assert principal == Principal(user="asha", role=Role.REVIEWER, token_id=token.split("_")[1])
    raw = store.path.read_text()
    assert token.split("_", 2)[2] not in raw  # only the hash is stored
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.revoke(token_id_of(token) or "")
    assert store.authenticate(token) is AuthFailure.REVOKED
    assert not store.revoke(token_id_of(token) or "")


def test_failures(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "tokens.json")
    assert store.authenticate(None) is AuthFailure.MISSING
    assert store.authenticate("Bearer x") is AuthFailure.MALFORMED
    token = store.create("ravi", Role.VIEWER, expires_days=1)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert store.authenticate(forged) is AuthFailure.UNKNOWN
    assert store.authenticate("ngocr_000000000000_" + "a" * 43) is AuthFailure.UNKNOWN
    data = json.loads(store.path.read_text())
    data["tokens"][0]["expires_at"] = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    store.path.write_text(json.dumps(data))
    assert store.authenticate(token) is AuthFailure.EXPIRED  # file changes are picked up
    assert token_id_of("nonsense") is None


def test_role_order() -> None:
    assert Role.ADMIN.allows(Role.APPROVER) and Role.APPROVER.allows(Role.REVIEWER)
    assert not Role.REVIEWER.allows(Role.APPROVER) and not Role.VIEWER.allows(Role.REVIEWER)


def test_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NGOCR_AUTH_TOKENS_FILE", str(tmp_path / "t.json"))
    get_settings.cache_clear()
    try:
        assert cli.main(["create", "--user", "meena", "--role", "approver"]) == 0
        token = capsys.readouterr().out.strip()
        assert TokenStore(tmp_path / "t.json").authenticate(token) is not None
        assert cli.main(["list"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["user"] == "meena" and "sha256" not in listed[0]
        assert cli.main(["revoke", listed[0]["id"]]) == 0
        assert cli.main(["revoke", listed[0]["id"]]) == 1
        with pytest.raises(SystemExit):
            cli.main(["create", "--user", "Bad User", "--role", "viewer"])
        with pytest.raises(SystemExit):
            cli.main(["create", "--user", "ok", "--role", "viewer", "--expires-days", "0"])
    finally:
        get_settings.cache_clear()
