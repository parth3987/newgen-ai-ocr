"""Web sign-in: user accounts with passwords, and server-side sessions (ADR-0030).

Passwords are hashed with scrypt from the standard library (no new dependency), with a
random salt per password. A session is a 256-bit random value in an HttpOnly,
SameSite=Strict cookie; only its SHA-256 is stored, so a database copy cannot be replayed
as a login. The CSRF token is derived from the session value, so it needs no storage and
cannot be computed without the cookie. Sessions expire after inactivity and after an
absolute lifetime; the user's current role and status are read on every request, so
disabling a user or changing a role applies at once.

Repeated wrong passwords lock the account for a while. An unknown user name costs the same
hash as a known one, so timing does not reveal which names exist.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, update

from newgen_ocr.auth.tokens import USER_PATTERN, Principal, Role
from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import User, UserSession
from newgen_ocr.errors import AppError, ErrorCode

# scrypt cost: 2^15 x 8 x 1 needs 32 MiB and about 0.1 s per hash on a laptop CPU.
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024
PASSWORD_MIN, PASSWORD_MAX = 12, 256
# Hash for unknown users, so a failed login takes the same time either way.
_DUMMY = None


def _now() -> datetime:
    return datetime.now(tz=UTC)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        computed = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(computed.hex(), digest)


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(16))
    return _DUMMY


def check_password_policy(password: str, user: str) -> None:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AppError(
            ErrorCode.WEAK_PASSWORD,
            f"Passwords need {PASSWORD_MIN} to {PASSWORD_MAX} characters",
        )
    if user.lower() in password.lower():
        raise AppError(ErrorCode.WEAK_PASSWORD, "The password must not contain the user name")


def session_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(session_value: str) -> str:
    """Bound to the session; the browser sends it back in ``X-CSRF-Token``."""
    return hashlib.sha256(f"csrf:{session_value}".encode()).hexdigest()


class UserInfo(BaseModel):
    name: str
    role: Role
    disabled: bool
    locked_until: datetime | None
    created_at: datetime
    password_changed_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True)
class SessionPolicy:
    idle: timedelta = timedelta(minutes=30)
    absolute: timedelta = timedelta(hours=12)
    max_failures: int = 5
    lockout: timedelta = timedelta(minutes=15)


@dataclass(frozen=True)
class LoginResult:
    session_value: str  # the cookie value: shown once, never stored
    principal: Principal
    expires_at: datetime


def _info(row: User) -> UserInfo:
    return UserInfo(
        name=row.name,
        role=Role(row.role),
        disabled=row.disabled,
        locked_until=row.locked_until,
        created_at=row.created_at,
        password_changed_at=row.password_changed_at,
        last_login_at=row.last_login_at,
    )


class UserStore:
    def __init__(self, db: Database, policy: SessionPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or SessionPolicy()

    # ------------------------------------------------------------ accounts

    def create(self, name: str, role: Role, password: str) -> UserInfo:
        if not re.fullmatch(USER_PATTERN, name):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid user name")
        check_password_policy(password, name)
        ts = _now()
        with self.db.session() as session:
            if session.get(User, name) is not None:
                raise AppError(ErrorCode.USER_EXISTS, "A user with that name exists")
            row = User(
                name=name,
                role=role.value,
                password_hash=hash_password(password),
                disabled=False,
                failed_logins=0,
                created_at=ts,
                password_changed_at=ts,
            )
            session.add(row)
            session.flush()
            return _info(row)

    def list(self) -> list[UserInfo]:
        with self.db.session() as session:
            return [_info(r) for r in session.scalars(select(User).order_by(User.name))]

    def get(self, name: str) -> UserInfo | None:
        with self.db.session() as session:
            row = session.get(User, name)
            return _info(row) if row is not None else None

    def update(
        self,
        name: str,
        *,
        role: Role | None = None,
        disabled: bool | None = None,
        password: str | None = None,
    ) -> UserInfo:
        """Change a user. Disabling or a new password ends their sessions; so does a role
        change (they sign in again with the new role)."""
        if password is not None:
            check_password_policy(password, name)
        with self.db.session() as session:
            row = session.get(User, name, with_for_update=True)
            if row is None:
                raise AppError(ErrorCode.USER_NOT_FOUND, "User not found")
            end_sessions = False
            if role is not None and role.value != row.role:
                row.role, end_sessions = role.value, True
            if disabled is not None and disabled != row.disabled:
                row.disabled = disabled
                end_sessions = end_sessions or disabled
            if password is not None:
                row.password_hash = hash_password(password)
                row.password_changed_at = _now()
                row.failed_logins, row.locked_until = 0, None
                end_sessions = True
            if end_sessions:
                session.execute(
                    update(UserSession)
                    .where(UserSession.user == name, UserSession.revoked_at.is_(None))
                    .values(revoked_at=_now())
                )
            session.flush()
            return _info(row)

    # ------------------------------------------------------------ sessions

    def login(self, name: str, password: str) -> LoginResult | str:
        """A new session, or the reason for refusing (``unknown``, ``wrong_password``,
        ``locked``, ``disabled``). Callers show one message for all of them."""
        ts = _now()
        with self.db.session() as session:
            row = session.get(User, name, with_for_update=True) if len(name) <= 64 else None
            if row is None:
                verify_password(password, _dummy_hash())
                return "unknown"
            if row.locked_until is not None and row.locked_until > ts:
                verify_password(password, _dummy_hash())
                return "locked"
            if not verify_password(password, row.password_hash):
                row.failed_logins += 1
                if row.failed_logins >= self.policy.max_failures:
                    row.locked_until, row.failed_logins = ts + self.policy.lockout, 0
                return "wrong_password"
            if row.disabled:
                return "disabled"
            row.failed_logins, row.locked_until, row.last_login_at = 0, None, ts
            value = secrets.token_urlsafe(32)
            expires = ts + self.policy.absolute
            session.add(
                UserSession(
                    id_hash=session_hash(value),
                    user=row.name,
                    created_at=ts,
                    last_seen_at=ts,
                    expires_at=expires,
                )
            )
            principal = Principal(
                user=row.name, role=Role(row.role), token_id=session_hash(value)[:12]
            )
            return LoginResult(value, principal, expires)

    def authenticate(self, value: str | None) -> Principal | None:
        """The session's principal with the user's current role, or None when the session
        is unknown, revoked, idle too long, past its lifetime, or the user is disabled."""
        if not value or len(value) > 128:
            return None
        ts = _now()
        with self.db.session() as session:
            found = session.execute(
                select(UserSession, User)
                .join(User, User.name == UserSession.user)
                .where(UserSession.id_hash == session_hash(value))
            ).first()
            if found is None:
                return None
            row, user = found
            if (
                row.revoked_at is not None
                or row.expires_at <= ts
                or row.last_seen_at + self.policy.idle <= ts
                or user.disabled
            ):
                return None
            if ts - row.last_seen_at > timedelta(seconds=60):  # fewer writes
                row.last_seen_at = ts
            return Principal(user=user.name, role=Role(user.role), token_id=row.id_hash[:12])

    def logout(self, value: str | None) -> None:
        if not value:
            return
        with self.db.session() as session:
            session.execute(
                update(UserSession)
                .where(UserSession.id_hash == session_hash(value), UserSession.revoked_at.is_(None))
                .values(revoked_at=_now())
            )

    def active_sessions(self) -> int:
        ts = _now()
        with self.db.session() as session:
            rows = session.scalars(
                select(UserSession.last_seen_at).where(
                    UserSession.revoked_at.is_(None), UserSession.expires_at > ts
                )
            ).all()
        return sum(1 for seen in rows if seen + self.policy.idle > ts)
