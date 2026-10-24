"""Manage API tokens (ADR-0027).

Usage::

    python -m newgen_ocr.auth create --user asha --role reviewer [--expires-days 90]
    python -m newgen_ocr.auth list
    python -m newgen_ocr.auth revoke <token-id>

    python -m newgen_ocr.auth user-create --user asha --role reviewer   # asks for a password
    python -m newgen_ocr.auth user-list
    python -m newgen_ocr.auth user-password --user asha
    python -m newgen_ocr.auth user-disable --user asha    (user-enable to undo)

Web users (ADR-0030) need the database. Passwords are read from the terminal without
echo, or from standard input with ``--password-stdin`` (one line), never from arguments.
Tokens are stored in PostgreSQL when ``NGOCR_DATABASE_URL`` is set (service mode),
otherwise in ``NGOCR_AUTH_TOKENS_FILE`` (default ``<data_dir>/auth/tokens.json``).
``create`` prints the token once; only its hash is stored.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys

from newgen_ocr.auth.db import DbTokenStore
from newgen_ocr.auth.tokens import USER_PATTERN, Role, TokenStore, describe, dumps
from newgen_ocr.auth.users import UserStore
from newgen_ocr.config import get_settings
from newgen_ocr.db.engine import Database
from newgen_ocr.errors import AppError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m newgen_ocr.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a token and print it once")
    create.add_argument("--user", required=True)
    create.add_argument("--role", required=True, choices=[r.value for r in Role])
    create.add_argument("--expires-days", type=int, default=None)
    sub.add_parser("list", help="list tokens (never the secrets)")
    revoke = sub.add_parser("revoke", help="revoke a token by its id")
    revoke.add_argument("token_id")
    for name in ("user-create", "user-password", "user-disable", "user-enable"):
        command = sub.add_parser(name)
        command.add_argument("--user", required=True)
        if name == "user-create":
            command.add_argument("--role", required=True, choices=[r.value for r in Role])
        if name in ("user-create", "user-password"):
            command.add_argument("--password-stdin", action="store_true")
    sub.add_parser("user-list", help="list web users")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command.startswith("user-"):
        if settings.database_url is None:
            parser.error("web users need NGOCR_DATABASE_URL (service mode)")
        return _users(args, UserStore(Database(settings.database_url, pool_size=1)))
    store: TokenStore | DbTokenStore
    if settings.database_url is not None:
        store = DbTokenStore(Database(settings.database_url, pool_size=1))
    else:
        store = TokenStore(settings.auth_tokens_path)
    if args.command == "create":
        if not re.fullmatch(USER_PATTERN, args.user):
            parser.error("user: lower-case letters, digits, '.', '_' or '-' (max 64)")
        if args.expires_days is not None and args.expires_days < 1:
            parser.error("--expires-days must be at least 1")
        token = store.create(args.user, Role(args.role), args.expires_days)
        sys.stdout.write(token + "\n")
        sys.stderr.write("Store this token now: it cannot be shown again.\n")
    elif args.command == "list":
        sys.stdout.write(dumps(describe(store.load())) + "\n")
    else:
        if not store.revoke(args.token_id):
            sys.stderr.write("no active token with that id\n")
            return 1
        sys.stdout.write(f"revoked {args.token_id}\n")
    return 0


def _password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("New password: ")
    if getpass.getpass("Repeat it: ") != first:
        raise SystemExit("the passwords differ")
    return first


def _users(args: argparse.Namespace, users: UserStore) -> int:
    try:
        if args.command == "user-list":
            rows = [u.model_dump(mode="json") for u in users.list()]
            sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        elif args.command == "user-create":
            users.create(args.user, Role(args.role), _password(args))
            sys.stdout.write(f"created {args.user}\n")
        elif args.command == "user-password":
            users.update(args.user, password=_password(args))
            sys.stdout.write(f"password changed for {args.user}; their sessions ended\n")
        else:
            users.update(args.user, disabled=args.command == "user-disable")
            sys.stdout.write(f"{args.command.removeprefix('user-')}d {args.user}\n")
    except AppError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
