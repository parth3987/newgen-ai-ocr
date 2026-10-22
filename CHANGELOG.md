# Changelog

All material changes by phase. Dates are ISO 8601.

## [Unreleased]

### Phase 2: Document ingestion and PDF processing (2026-10-22)
- `POST /api/v1/documents`, `GET /api/v1/documents/{id}`, `.../pages/{n}/text` and
  `.../pages/{n}/image`.
- Signature-based detection of PDF/JPEG/PNG/TIFF; extension cross-check; size, page and pixel
  limits; streaming request-body limit; corrupt, truncated and encrypted file handling with
  new stable error codes.
- Per-page native-text detection for PDFs (native pages are not rendered); embedded text with
  segment bounding boxes; rendering of scanned pages at a configurable DPI.
- Image normalisation (EXIF orientation, alpha on white, 16-bit scaling); multi-frame TIFF.
- Local document store: quarantine → staging → atomic commit; originals separate from derived.
- JSONL audit trail for accepted and rejected documents.
- `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on all responses.
- FastAPI built-in telemetry explicitly disabled, with a policy test.
- Release-age rule enforced in `make lock` via `--exclude-newer`. Locks re-resolved
  (FastAPI 0.143.0, Starlette 1.7.0). Security overrides: Pillow 13.0.0, certifi.
- New runtime dependencies: pypdfium2 5.14.0, Pillow 13.0.0, python-multipart 0.0.32.
- Error handling split: framework-free `newgen_ocr.errors`, HTTP mapping in `newgen_ocr.api.errors`.
- Implementation plan renumbered: persistence and jobs move to Phase 3.

### Phase 1: Development foundation (2026-10-22)
- Python 3.11 project (`pyproject.toml`) with Ruff, strict mypy, pytest and a 90% coverage gate.
- Hash-pinned `requirements.lock` / `requirements-dev.lock` generated from `requirements*.in`.
- FastAPI app factory, `NGOCR_*` settings, JSON structured logging with request ids, stable
  error envelope.
- `/health/live` and `/health/ready` endpoints.
- Policy tests: no hosted-AI or telemetry SDKs, no network-client imports, CDN docs UI disabled.
- Tooling: Makefile, license allowlist script, Bandit, pip-audit, Gitleaks, CycloneDX SBOM,
  GitHub Actions CI.
- `.env.example`; `.gitignore` extended for `data/`, caches and reports.

### Phase 0: Planning (2026-10-22)
- Repository and host inspection; architecture, implementation plan, decisions, security,
  testing, evaluation, model inventory, offline deployment and runbook documents.
