"""Database engine and sessions (ADR-0029).

``hide_parameters`` keeps bound values (which may be document content or results) out of
SQLAlchemy exception messages, and so out of logs. The URL is a ``SecretStr`` and is never
logged; only the host and database name appear in startup logs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


def safe_url(url: SecretStr) -> str:
    """Host, port and database only, for logs."""
    parsed = make_url(url.get_secret_value())
    return f"{parsed.drivername}://{parsed.host}:{parsed.port}/{parsed.database}"


class Database:
    def __init__(self, url: SecretStr, pool_size: int = 5) -> None:
        self.url = url
        self.engine: Engine = create_engine(
            url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=pool_size,
            hide_parameters=True,
            connect_args={"connect_timeout": 10, "application_name": "newgen-ocr"},
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """One transaction: committed on success, rolled back on any exception."""
        with self._sessions() as session, session.begin():
            yield session

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - readiness probe: any failure means not ready
            return False

    def dispose(self) -> None:
        self.engine.dispose()
