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
make check       # lint + typecheck + test + security + licenses
```

Run a single test: `.venv/bin/pytest tests/unit/test_health.py::test_live_returns_ok_and_version`.

## Layout

```
tests/
  conftest.py        settings/app/client fixtures (tmp data dir, no .env loading)
  fixtures/builders.py  synthetic documents: hand-built PDFs (text layers, embedded JPEG
                     "scans", invisible OCR layers, RC4-encrypted), JPEG/PNG/TIFF images
  fixtures/pages.py  synthetic A4 text pages and degradations: rotation, quarter turns,
                     Gaussian and salt-and-pepper noise, blur, fading, shadow, blank
  unit/              fast, no external services
  integration/       HTTP API + real local storage (Phase 2); Postgres/Valkey [planned P5]
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
