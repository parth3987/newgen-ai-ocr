# Testing

## Commands

```bash
make venv        # create .venv from the hash-pinned locks (Python 3.11)
make lint        # ruff format --check + ruff check
make typecheck   # mypy --strict on src/newgen_ocr
make test        # pytest with coverage (fails under 90%)
make security    # bandit + pip-audit (both locks)
make licenses    # runtime license allowlist
make secrets     # gitleaks (git history + working tree; needs Docker)
make sbom        # CycloneDX SBOM -> reports/
make check       # lint + typecheck + test + security + licenses
```

Run a single test: `.venv/bin/pytest tests/unit/test_health.py::test_live_returns_ok_and_version`.

## Layout

```
tests/
  conftest.py        settings/app/client fixtures (tmp data dir, no .env loading)
  fixtures/builders.py  synthetic documents: hand-built PDFs (text layers, embedded JPEG
                     "scans", invisible OCR layers, RC4-encrypted), JPEG/PNG/TIFF images
  unit/              fast, no external services
  integration/       HTTP API + real local storage (Phase 2); Postgres/Valkey [planned P3]
  e2e/               upload → OCR → export on fixtures       [planned P5+]
```

## Rules

- Tests construct `Settings(..., _env_file=None)` explicitly; a developer's `.env` never
  influences results.
- No real documents in the repository. Fixtures are synthetic or explicitly licensed.
- Policy tests in `tests/unit/test_offline_policy.py` enforce: no hosted-AI or telemetry SDKs
  installed, no direct network-client imports in `src/`, CDN-backed docs UI disabled.
- A test result is reported only after it has been executed and its output inspected.
- Rejection tests assert both the error code and that no file remains in quarantine,
  staging, originals or derived storage.
- Fixtures are generated in code, so there are no binary blobs in the repository and every
  page's content (text layer, image, visibility, encryption) is controlled exactly.
- Accuracy measurement is separate from unit testing. See ACCURACY_EVALUATION.md.

## Known warnings

- `StarletteDeprecationWarning`: Starlette prefers `httpx2` for `TestClient`. Migration is
  pending approval (DECISIONS.md OD-7).
