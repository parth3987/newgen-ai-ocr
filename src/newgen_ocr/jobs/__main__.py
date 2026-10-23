"""Job maintenance: ``python -m newgen_ocr.jobs reap`` (service mode).

``reap`` runs one reaper pass now (workers also do this every minute): jobs whose worker
died are requeued or failed, and queued jobs whose message was lost are dispatched again.
"""

from __future__ import annotations

import argparse
import json
import sys

from newgen_ocr.config import get_settings
from newgen_ocr.jobs.celery_app import make_celery, reap_once
from newgen_ocr.services import build_services


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m newgen_ocr.jobs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reap", help="recover jobs from dead workers and lost messages")
    parser.parse_args(argv)
    settings = get_settings()
    if not settings.service_mode:
        parser.error("needs NGOCR_DATABASE_URL and NGOCR_BROKER_URL (service mode)")
    redispatch, failed = reap_once(build_services(settings, pool_size=1), make_celery(settings))
    sys.stdout.write(json.dumps({"redispatched": redispatch, "failed": failed}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
