"""Database migrations (ADR-0029).

Usage::

    python -m newgen_ocr.db upgrade [revision]     # default: head
    python -m newgen_ocr.db downgrade <revision>   # e.g. base
    python -m newgen_ocr.db current

Uses NGOCR_DATABASE_URL. The migration scripts ship inside the package, so this works from
an installed wheel without alembic.ini.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from newgen_ocr.db.engine import Database

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if url is not None:
        config.attributes["url"] = url
    return config


def schema_is_current(db: Database) -> bool:
    """The database is at the latest migration (readiness: never serve an old schema)."""
    try:
        heads = set(ScriptDirectory.from_config(alembic_config()).get_heads())
        with db.engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        return current == heads
    except Exception:  # noqa: BLE001 - readiness probe: any failure means not ready
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m newgen_ocr.db")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade")
    up.add_argument("revision", nargs="?", default="head")
    down = sub.add_parser("downgrade")
    down.add_argument("revision")
    sub.add_parser("current")
    args = parser.parse_args(argv)
    config = alembic_config()
    if args.command == "upgrade":
        command.upgrade(config, args.revision)
    elif args.command == "downgrade":
        command.downgrade(config, args.revision)
    else:
        command.current(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
