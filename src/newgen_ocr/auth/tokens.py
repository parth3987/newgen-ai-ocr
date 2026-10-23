"""Local bearer tokens with roles (ADR-0027).

Tokens look like ``ngocr_<id>_<secret>``. Only a SHA-256 hash of the secret is stored, in a
JSON file managed with ``python -m newgen_ocr.auth``. The secret is 256 bits from
``secrets``, so a fast hash is sufficient (nothing to brute-force); comparison is constant
time. The file is re-read when it changes, so revocations apply without a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

USER_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_TOKEN = re.compile(r"^ngocr_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")


class Role(StrEnum):
    VIEWER = "viewer"  # read the queue, items, crops and history
    REVIEWER = "reviewer"  # + claim, correct, confirm, reject
    APPROVER = "approver"  # + approve (never their own corrections)
    ADMIN = "admin"  # + reassign, verify the history chain

    @property
    def rank(self) -> int:
        return list(Role).index(self)

    def allows(self, required: Role) -> bool:
        return self.rank >= required.rank


class TokenRecord(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    user: str = Field(pattern=USER_PATTERN)
    role: Role
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class TokenFile(BaseModel):
    tokens: list[TokenRecord] = []


class Principal(BaseModel):
    user: str
    role: Role
    token_id: str


class AuthFailure(StrEnum):
    MISSING = "missing"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    REVOKED = "revoked"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[float, TokenFile] | None = None

    def load(self) -> TokenFile:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                return TokenFile()
            if self._cache is None or self._cache[0] != mtime:
                self._cache = (mtime, TokenFile.model_validate_json(self.path.read_bytes()))
            return self._cache[1]

    def _save(self, data: TokenFile) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.model_dump_json(indent=2).encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def create(self, user: str, role: Role, expires_days: int | None = None) -> str:
        """Add a token and return it. The token is shown once and never stored."""
        data = self.load().model_copy(deep=True)
        token_id = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        now = datetime.now(tz=UTC)
        data.tokens.append(
            TokenRecord(
                id=token_id,
                user=user,
                role=role,
                sha256=_digest(secret),
                created_at=now,
                expires_at=now + timedelta(days=expires_days) if expires_days else None,
            )
        )
        self._save(data)
        return f"ngocr_{token_id}_{secret}"

    def revoke(self, token_id: str) -> bool:
        data = self.load().model_copy(deep=True)
        for record in data.tokens:
            if record.id == token_id and record.revoked_at is None:
                record.revoked_at = datetime.now(tz=UTC)
                self._save(data)
                return True
        return False

    def authenticate(self, token: str | None) -> Principal | AuthFailure:
        if not token:
            return AuthFailure.MISSING
        match = _TOKEN.fullmatch(token)
        if match is None:
            return AuthFailure.MALFORMED
        token_id, secret = match.groups()
        record = next((r for r in self.load().tokens if r.id == token_id), None)
        if record is None or not hmac.compare_digest(record.sha256, _digest(secret)):
            return AuthFailure.UNKNOWN
        if record.revoked_at is not None:
            return AuthFailure.REVOKED
        if record.expires_at is not None and record.expires_at <= datetime.now(tz=UTC):
            return AuthFailure.EXPIRED
        return Principal(user=record.user, role=record.role, token_id=record.id)


def token_id_of(token: str | None) -> str | None:
    """The public id part of a well-formed token (for audit records), else None."""
    match = _TOKEN.fullmatch(token or "")
    return match.group(1) if match else None


def describe(data: TokenFile) -> list[dict[str, str | None]]:
    return [
        {
            "id": r.id,
            "user": r.user,
            "role": r.role.value,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
        }
        for r in data.tokens
    ]


def dumps(rows: list[dict[str, str | None]]) -> str:
    return json.dumps(rows, indent=2)
