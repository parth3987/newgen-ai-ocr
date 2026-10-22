# Changelog

All material changes by phase. Dates are ISO 8601.

## [Unreleased]

### Phase 3: Image preprocessing and quality assessment (2026-10-22)
- Quality assessment for every page image, recorded in the manifest: blank, low/unknown
  resolution, blur, poor contrast, Gaussian and salt-and-pepper noise, uneven background,
  skew and rotation, each with its metric.
- Text-axis (90°) and skew detection by projection profiles (±0.15° on synthetic pages).
- Preprocessing profiles `none`, `standard` (flag-driven), `degraded_scan`, `photo`,
  `binarize`, plus custom profiles via environment; steps for orientation, deskew,
  illumination flattening, denoise, contrast (stretch + CLAHE) and adaptive threshold.
- Per-profile runs stored beside immutable source pages; every step's decision, parameters
  and timing recorded; re-run endpoint; `preprocessing.completed/failed` audit events.
- API: `profile` field on upload; `/api/v1/preprocessing/profiles`;
  `/api/v1/documents/{id}/preprocessing[/{profile}[/pages/{n}/image]]`.
- OpenCV 4.14 (PyPI, development only) and NumPy 2.4.6. Production startup guard rejects
  FFmpeg-enabled OpenCV builds (ADR-0015).
- CPU benchmark script, `make bench` and docs/PERFORMANCE.md. Measured optimisations:
  end-to-end 1.9 → 1.2 s/page (PNG level 3, low-resolution background estimation).
- `pip-audit` runs with `--disable-pip` (no package downloads).
- Test PDF builder declares greyscale JPEGs correctly.
- Implementation plan renumbered: persistence and jobs move to Phase 4.

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
