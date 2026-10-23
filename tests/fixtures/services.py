"""Service-mode settings and databases for tests (ADR-0029)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Unreachable placeholders: for tests that build production settings but fail (as intended)
# before anything connects. No credentials.
PLACEHOLDER_URLS = {
    "database_url": "postgresql+psycopg://ngocr@127.0.0.1:9/ngocr",
    "broker_url": "redis://127.0.0.1:9/0",
}

# A real PostgreSQL and Valkey for the service-mode tests (``make services-up``); the tests
# that need them are skipped when these are unset.
TEST_DATABASE_URL = os.environ.get("NGOCR_TEST_DATABASE_URL")
TEST_BROKER_URL = os.environ.get("NGOCR_TEST_BROKER_URL")


def require_database() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("NGOCR_TEST_DATABASE_URL not set (make services-up)")
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:  # libpq missing on the host (sudo apt install libpq5)
        pytest.skip(f"psycopg cannot load libpq: {exc}")
    return TEST_DATABASE_URL


def _admin(url: str):  # type: ignore[no-untyped-def]
    return create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)


@pytest.fixture(scope="session")
def template_database() -> Iterator[str]:
    """A migrated database that each test copies (``CREATE DATABASE ... TEMPLATE``)."""
    from newgen_ocr.db.__main__ import alembic_config

    url = require_database()
    name = f"ngocr_tmpl_{uuid.uuid4().hex[:8]}"
    admin = _admin(url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(url).set(database=name).render_as_string(hide_password=False)
    from alembic import command

    command.upgrade(alembic_config(target), "head")
    try:
        yield name
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def database_url(template_database: str) -> Iterator[str]:
    """A fresh, migrated database for one test, dropped afterwards."""
    url = require_database()
    name = f"ngocr_test_{uuid.uuid4().hex[:12]}"
    admin = _admin(url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template_database}"'))
    try:
        yield make_url(url).set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
