"""Alembic environment: migrations run against NGOCR_DATABASE_URL (ADR-0029)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from newgen_ocr.config import get_settings
from newgen_ocr.db.models import Base

config = context.config


def _url() -> str:
    explicit = config.attributes.get("url")
    if explicit:
        return str(explicit)
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("NGOCR_DATABASE_URL is not set")
    return url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = create_engine(_url(), poolclass=pool.NullPool, hide_parameters=True)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
        return
    context.configure(connection=connectable, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
