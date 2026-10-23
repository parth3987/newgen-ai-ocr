"""Tokens in PostgreSQL (service mode, ADR-0029); same behaviour as the file store."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from newgen_ocr.auth.tokens import (
    AuthFailure,
    Principal,
    Role,
    TokenFile,
    TokenRecord,
    authenticate,
    new_token,
)
from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import ApiToken


def _record(row: ApiToken) -> TokenRecord:
    return TokenRecord(
        id=row.id,
        user=row.user,
        role=Role(row.role),
        sha256=row.sha256,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class DbTokenStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self) -> TokenFile:
        with self.db.session() as session:
            rows = session.scalars(select(ApiToken).order_by(ApiToken.created_at)).all()
            return TokenFile(tokens=[_record(r) for r in rows])

    def find(self, token_id: str) -> TokenRecord | None:
        with self.db.session() as session:
            row = session.get(ApiToken, token_id)
            return _record(row) if row is not None else None

    def create(self, user: str, role: Role, expires_days: int | None = None) -> str:
        token, record = new_token(user, role, expires_days)
        with self.db.session() as session:
            session.add(ApiToken(**record.model_dump(mode="python")))
        return token

    def revoke(self, token_id: str) -> bool:
        with self.db.session() as session:
            row = session.get(ApiToken, token_id, with_for_update=True)
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = datetime.now(tz=UTC)
            return True

    def authenticate(self, token: str | None) -> Principal | AuthFailure:
        return authenticate(token, self.find)
