"""Container health check: ``python -m newgen_ocr.healthcheck`` (ADR-0031).

Exits 0 when the local API reports ready (``/health/ready``), 1 otherwise. A module rather
than an inline ``python -c`` so the compose command has no spaces to be split.
"""

from __future__ import annotations

import urllib.request

URL = "http://127.0.0.1:8000/health/ready"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=5) as response:  # nosec B310 - fixed local URL
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
