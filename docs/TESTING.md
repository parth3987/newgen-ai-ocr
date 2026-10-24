# Testing

## Commands

```bash
make venv        # create .venv from the hash-pinned locks (Python 3.11)
make lint        # ruff format --check + ruff check
make typecheck   # mypy --strict on src/newgen_ocr
make test        # pytest with coverage (fails under 90%)
make security    # bandit + pip-audit (both locks, --disable-pip: no package downloads)
make licenses    # runtime license allowlist
make secrets     # gitleaks (git history + working tree; needs Docker)
make sbom        # CycloneDX SBOM -> reports/
make bench       # CPU benchmark of assessment/preprocessing (docs/PERFORMANCE.md)
make models      # STAGING (network): fetch + verify pinned OCR models into models/
make eval        # OCR accuracy (CER/WER) + throughput on the synthetic corpus
make fonts       # STAGING (network): fetch pinned OFL handwriting fonts (evaluation only)
make eval-languages    # script/orientation ID, hi/mr ID, mixed-script routing
make eval-handwriting  # handwriting (synthetic fonts; --dataset DIR for real samples)
make eval-layout # layout, tables, reading order, key-value pairs (synthetic layout set)
make eval-extraction   # classification, fields, tables, checks (synthetic extraction set)
make schemas     # regenerate docs/schemas/ and EXTRACTION_SCHEMAS.md (a test checks drift)
make calibrate   # fit review thresholds on the synthetic calibration set (about 2 h, CPU)
make services-up # PostgreSQL + Valkey on 127.0.0.1 (podman or docker); service-mode tests
make services-down
make frontend-test  # frontend: tsc type check, Vitest unit tests, npm licence check
make frontend    # STAGING (network for npm ci): build the web application
make check       # lint + typecheck + services-up + test + frontend-test + security + licenses
```

**Frontend tests** (`frontend/src/**/*.test.ts(x)`, Vitest with jsdom) mock `fetch` and cover:
- the API client (CSRF only on changes, error messages with references, sign-out on `401`);
- status and progress display, and text never rendered as markup;
- sign-in failures, role-dependent navigation, and pages refused without calling the API;
- returning to sign-in when a session expires.

The access rules themselves are tested against the API
(`tests/integration/test_web_signin.py`), never only in the browser.

**Service-mode tests** (`tests/integration/test_jobs.py`, `test_worker.py`) run against the
real PostgreSQL and Valkey from `make services-up`. `make test` passes their URLs from
`deploy/secrets/` as `NGOCR_TEST_DATABASE_URL` and `NGOCR_TEST_BROKER_URL`. Each test gets
a fresh database, copied from a migrated template and dropped afterwards; Valkey database
15 is flushed. Without the URLs, or without the OS libpq (`sudo apt install libpq5`), these
tests are **skipped**, and pytest reports them as skipped. `test_worker.py` starts the real
worker (`NGOCR_TEST_WORKER_CMD` overrides the command) and kills it in the middle of a job.

Run a single test: `.venv/bin/pytest tests/unit/test_health.py::test_live_returns_ok_and_version`.

## Layout

```
tests/
  conftest.py        settings/app/client fixtures (tmp data dir, no .env loading)
  fixtures/builders.py  synthetic documents: hand-built PDFs (text layers, embedded JPEG
                     "scans", invisible OCR layers, RC4-encrypted), JPEG/PNG/TIFF images
  fixtures/pages.py  synthetic A4 text pages and degradations: rotation, quarter turns,
                     Gaussian and salt-and-pepper noise, blur, fading, shadow, blank
  fixtures/ocr.py    fake OCR engines and script detector
  fixtures/layout.py fake layout models, a drawn ruled-table page, SLANet-style tokens
  fixtures/extraction.py documents from text lines: in-memory views or stored OCR/layout runs
  fixtures/services.py  per-test PostgreSQL databases (migrated template) for service mode
  unit/              fast, no external services
  integration/       HTTP API + real local storage; service mode on real Postgres/Valkey
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
- Preprocessing tests assert outcomes against *measurements* (e.g. the re-measured skew
  after deskew, flags clearing in `quality_after`), not just that a step ran.
- Quality thresholds are calibrated on synthetic pages. Tests check both that each
  degradation is flagged and that milder variants are not.
- **Markers:** `models` tests need `make models` (auto-skipped if models are absent);
  `tesseract` tests need the `tesseract` binary (auto-skipped if missing). CI installs both.
- **Offline proof:** `test_paddle_ocr_runs_with_no_network_namespace` runs OCR under
  `unshare -rn` (no network interfaces) with an audit hook asserting zero socket
  connect/DNS events; `test_paddlex_cannot_resolve_or_download_models` proves the model
  manager refuses name-based resolution.
- OCR service and API policy (fallback, comparison, skips, errors) are tested with
  `tests/fixtures/ocr.py::FakeEngine`; real-engine behaviour is tested separately.
- Accuracy measurement is separate from unit testing. See ACCURACY_EVALUATION.md.

## Known warnings

- `StarletteDeprecationWarning`: Starlette prefers `httpx2` for `TestClient`. Migration is
  pending approval (DECISIONS.md OD-7).
