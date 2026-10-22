# Architecture Decisions

## Accepted

### ADR-0001: Python 3.11, uv and hash-pinned lock files (2026-10-22)
Python 3.11 matches the mandated runtime and has the widest support among the CPU OCR
stacks (PaddlePaddle 3.2.2 verified in Phase 4, ADR-0020). The dev host ships
3.14, so 3.11 is provisioned by uv. `requirements*.in` are the human-edited sources;
`uv pip compile --generate-hashes` produces `requirements*.lock`, installed with
`--require-hashes`. Locks are resolved for `x86_64-manylinux_2_28` / Python 3.11 regardless
of the host. `pyproject.toml` declares no dependencies, so there is a single source of truth.

### ADR-0002: `src/` layout, single package, app factory (2026-10-22)
One importable package `newgen_ocr` with subpackages per pipeline stage (ARCHITECTURE.md §3).
The FastAPI app is built by `create_app(settings)`, with no module-level app instance, so
tests inject settings and nothing reads the environment at import time.

### ADR-0003: Configuration via pydantic-settings (2026-10-22)
Environment variables prefixed `NGOCR_`; optional `.env` in development; the settings object
is frozen. Secrets will be `SecretStr` with no defaults.

### ADR-0004: Structured logging with the standard library (2026-10-22)
JSON lines to stdout (container-friendly), with no extra dependency. Policy: metadata only,
never document contents or field values. Query strings are not logged. HTTP-client
libraries (`httpx`, `httpcore`, `urllib3`) are capped at WARNING because they log full URLs.
The handler writes to the *current* `sys.stdout` at emit time so redirection works.

### ADR-0005: Disable Swagger UI and ReDoc (2026-10-22)
FastAPI's interactive docs load JavaScript and CSS from public CDNs, which violates the
no-external-dependency policy. The OpenAPI JSON is still served at `/api/v1/openapi.json`.
If interactive docs are wanted later, vendor the assets locally.

### ADR-0006: Error envelope and stable error codes (2026-10-22)
All errors return `{"error": {"code", "message", "request_id"}}`. Codes are a `StrEnum` and
are never renamed or reused. Validation errors report field locations and messages but never
echo input values. Unhandled exceptions return a generic 500.

### ADR-0007: Dependency admission policy (2026-10-22)
A new dependency (Python, npm, container image or model) needs: a license on the allowlist
(`scripts/check_licenses.py`) or explicit approval; a clean `pip-audit`; a release at least
14 days old; an entry in THIRD_PARTY_NOTICES.md (and MODEL_INVENTORY.md for models).

*Amended 2026-10-22 (Phase 2).* The Phase 1 locks violated the age rule (FastAPI 0.159.1,
Starlette 1.9.0, Pydantic 2.14.1, Ruff and others were under 14 days old) because ages were
checked by hand only for directly added packages. `make lock` now enforces the rule for every
transitive dependency with `uv pip compile --exclude-newer <today-14d> --upgrade`, which moved
FastAPI to 0.143.0 and Starlette to 1.7.0. **Security exception:** when the age-floor version
has known vulnerabilities fixed only in a newer release, that package gets a dated
`--exclude-newer-package` override in `SECURITY_OVERRIDES` (Makefile). Current overrides:
`pillow` (12.3.0 has about 25 CVEs, including TIFF decoder out-of-bounds reads, fixed in
13.0.0) and `certifi` (dev only). Remove each override once the floor passes it.
`pip-audit` runs with `--disable-pip` against the fully hashed locks, so it queries vulnerabilities without downloading packages.

### ADR-0008: `opentelemetry-api` dependency; FastAPI telemetry disabled (2026-10-22)
FastAPI requires `opentelemetry-api` (Apache-2.0), which is a no-op without an SDK and
exporter. FastAPI also ships built-in instrumentation (`fastapi.telemetry`). With
`FASTAPI_OTEL_AUTO_CONFIGURE=true` and an `OTEL_EXPORTER_OTLP_*` endpoint in the environment,
it attaches OTLP exporters (verified 2026-10-22: a default `FastAPI()` enables all signals
under that variable). Controls: `create_app` passes
`telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False}`;
`tests/unit/test_offline_policy.py` asserts that this holds even with those variables set,
and fails if any OpenTelemetry SDK/exporter/instrumentation or hosted-AI SDK is installed.
Containers will also set `OTEL_SDK_DISABLED=true`.

### ADR-0009: Separate liveness and readiness probes (2026-10-22)
`/health/live` performs no I/O (it is for restart decisions). `/health/ready` checks the
dependencies needed to accept work (currently `data_dir`; DB and broker are added in Phase 5)
and returns 503 with per-check results when any check fails.

### ADR-0010: Approved formats detected by signature (2026-10-22)
Accepted inputs are PDF, JPEG, PNG and classic TIFF (not BigTIFF), configurable down through
`NGOCR_ALLOWED_FORMATS`. The format is decided by magic bytes at offset 0 only. A filename
extension that contradicts the detected format is rejected (`FORMAT_MISMATCH`), and the
declared Content-Type is recorded but not trusted. Pillow is invoked with `formats=[<detected>]`,
so decoders for other formats (EPS, GIF, XBM, ...) are unreachable. PDFs that need a user
password are rejected (`ENCRYPTED_DOCUMENT`). Permissions-only (owner-password) PDFs are
accepted and flagged `pdf_encrypted: true`.

### ADR-0011: Per-page OCR decision for PDFs (2026-10-22)
Each PDF page is classified from measurements of its text layer (`newgen_ocr/pdf/analysis.py`).
In order: no text → OCR (`no_text_layer`); ≥90% of text objects invisible (render mode 3, a
third-party OCR layer) → OCR (`invisible_text_layer`, embedded text kept for comparison);
>10% replacement/control/private-use characters → OCR (`unreliable_text`); fewer than
`NGOCR_NATIVE_TEXT_MIN_CHARS` (20) characters → OCR (`insufficient_text`); raster images
covering ≥50% of the page → OCR (`image_content`, which catches scans with a digital header
line); otherwise the native text is used and the page is **not rendered**. This trades some
unnecessary OCR on image-heavy digital pages for never silently losing scanned content.
Thresholds are named constants and will be tuned against the evaluation set (Phase 5).

### ADR-0012: Synchronous ingestion with a global PDFium lock (temporary) (2026-10-22)
Until the job queue exists (Phase 5), `POST /api/v1/documents` ingests synchronously in
FastAPI's thread pool. PDFium is not thread-safe, so all PDFium use is serialised by
`PDFIUM_LOCK`. Throughput is therefore limited to one PDF at a time per process, which is
acceptable for development and must not be used for production volumes.

### ADR-0013: Filesystem document store and manifests as the metadata record (2026-10-22)
Until PostgreSQL arrives (Phase 5), `derived/<id>/manifest.json` is the system of record for
document metadata. Originals (`originals/`, mode 0440) are stored separately from derived
artefacts (`derived/`). Derived output is built in `staging/` and renamed into place
atomically, with the manifest included. Client filenames never touch the filesystem. Document
ids are random UUID4 hex strings. Duplicate uploads become separate documents (sha256 is
recorded so they can be deduplicated later).

### ADR-0014: Append-only JSONL audit trail (2026-10-22)
`audit/audit.jsonl` records `document.accepted` and `document.rejected` with request id,
actor (`anonymous` until auth exists), sha256, size, format, page counts and error code,
never filenames or content. Each line is appended with `O_APPEND` and fsync. It moves to a
database table in Phase 5, with the file kept as a tamper-evident export.

### ADR-0015: OpenCV supplied as a minimal source build in production (2026-10-22)
Decided by the project owner. The PyPI `opencv-python-headless` 4.14.0.94 wheel loads 16
bundled native libraries into the process at `import cv2`. They include FFmpeg (LGPL-2.1),
libvpx/libaom/dav1d codecs, the GCC Fortran runtime, OpenBLAS and OpenSSL `1.1.1k-17.el8_6`.
That OpenSSL is a Red Hat/AlmaLinux 8 vendor build; upstream 1.1.1 is end-of-life, and which
security backports this build carries is unverified. None of these are used by this
application. PaddleOCR 3.7 (Phase 5) separately pins `opencv-contrib-python==4.10.0.84`.
**Decision:** production uses OpenCV 4.x built from source in a reproducible container with
only the modules we need (core, imgproc, photo), without FFmpeg/videoio/OpenSSL. It is
published as an internal hash-pinned wheel that also satisfies PaddleOCR's `cv2`
requirement. Development and CI use the PyPI wheel. **Enforcement:** `create_app` refuses
to start with `NGOCR_ENVIRONMENT=production` if `cv2.getBuildInformation()` reports
`FFMPEG: YES` (`newgen_ocr/imaging/runtime.py`, tested). The build itself is a Phase 5/10 task.

### ADR-0016: Orientation and skew by projection profiles; 180° resolved at OCR (2026-10-22)
Skew is the angle that maximises the sharpness of the ink projection profile (coarse 0.5°
then fine 0.05° search over ±15°, ≤40k sampled ink points, on a 150 DPI analysis image).
Text-line axis compares row and column profile sharpness (measured: about 9–12 for
horizontal text, 0.08 for text rotated 90°). Vertical text is turned 90° clockwise. Projection
profiles **cannot distinguish 0° from 180°, or 90° from 270°**. That ambiguity is left to the
OCR stage (PaddleOCR's text-direction classifier, or comparing OCR confidence on candidate
orientations, in Phase 5), and is not guessed here. Skew estimates below confidence 2.0 are
treated as undetermined (calibration: non-text content about 1.1; text 3.3 to 115). Measured
accuracy on synthetic pages: within ±0.15° from −12° to +9°, including a JPEG-compressed
2× upscaled scan.

### ADR-0017: Quality flags from calibrated heuristics (2026-10-22)
Flags: `blank_page`, `low_resolution`, `resolution_unknown`, `blurry`, `poor_contrast`,
`noisy`, `uneven_background`, `skewed`, `rotated`. Each is computed from a named metric with
a named threshold (`newgen_ocr/imaging/quality.py`), and all metrics are recorded alongside
the flags. Pixel-noise metrics are measured at native resolution, because downscaling
averaged σ=20 noise down to a reading of 5.4. Salt-and-pepper is counted as *isolated* dark
pixels, because counting pixels that differ from their median flagged every clean page
(thin strokes). DPI metadata below 100 on large images is treated as unknown (camera
defaults). Thresholds were calibrated on synthetic pages only. They are heuristics, will be
re-tuned on the evaluation set (Phase 5), and make no accuracy claims.

### ADR-0018: Profiles, flag-driven enhancement, and source preservation (2026-10-22)
Preprocessing is selected by named profiles (`none`, `standard`, `degraded_scan`, `photo`,
`binarize`, plus custom profiles via `NGOCR_CUSTOM_PREPROCESSING_PROFILES`). The default
`standard` profile applies denoise and contrast only when the matching quality flag is set
(`auto`), and never thresholds. The source page image (`derived/<id>/pages/`) is **never
modified**. Each profile's output goes to `derived/<id>/preprocessed/<profile>/`, is swapped
in atomically, and can be re-run. A page that no step changed has no output image (`output:
null`), and OCR uses the source. Every step records `applied`, `reason`, `params` and
`duration_ms`, and processed pages are re-assessed (`quality_after`). Contrast enhancement is
a robust global stretch followed by CLAHE, because CLAHE alone (by design) raised a faded
page's contrast only from 38 to 48. Preprocessing after upload is best-effort: a failure is
logged and audited (`preprocessing.failed`) and does not fail the stored upload.

### ADR-0019: PNG zlib level 3 for stored page images (2026-10-22)
PNG is lossless at every level. Level 3 measured 43% faster than the default level 6 on
300 DPI RGB pages, with equal or smaller files (docs/PERFORMANCE.md).

### ADR-0020: Official PaddleOCR stack, locked down for offline use (2026-10-22)
Decided by the project owner: use PaddleOCR 3.7.0 / PaddleX 3.7.2 as published rather than
custom glue or ONNX. This adds 56 transitive runtime packages, including model-hub SDKs
(`huggingface-hub`, `modelscope`, `aistudio-sdk`, `bce-python-sdk`) that are installed but
never invoked. Controls, all in `newgen_ocr/ocr/paddle_runtime.py` (the only module that
imports PaddleX):
1. Offline environment flags are set before import (`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`,
   `HF_HUB_OFFLINE`, `MODELSCOPE_OFFLINE`, telemetry off). PaddleX's cache home is inside
   `data/cache/paddlex`.
2. PaddleX's official-model manager is disabled: any attempt to resolve a model by name raises
   `OfflineModelResolutionError`. Every model is passed as an explicit, hash-verified
   directory.
3. `opencv-contrib-python` (pinned by PaddleX, a second `cv2` distribution) is removed by a uv
   override. PaddleX's distribution-name checks are pointed at `opencv-python-headless`,
   during import (41 modules decide at import time) and afterwards. A source search shows
   PaddleX's OCR code uses no contrib-only OpenCV modules.
4. These patches touch PaddleX internals, so they refuse to run on any version other than
   the pinned PaddleX/PaddleOCR.

Verified by tests: OCR inside a network namespace with no network interfaces, an audit hook
that records zero socket connect/DNS attempts during import and OCR, and a probe proving the
model manager refuses to resolve models. **PaddlePaddle is pinned to 3.2.2**, not 3.3.1:
3.3.1's PIR + oneDNN executor fails on PP-OCRv5 detection
(`ConvertPirAttribute2RuntimeAttribute`), and without oneDNN a page took 35 s instead of
6.9 s (measured, same machine). `crc32c` (LGPL, optional in the Baidu SDK) is removed by
override; `python-bidi` (LGPL, required by the recogniser), `certifi` and `tqdm` (MPL-2.0) are
reviewed exceptions (THIRD_PARTY_NOTICES.md).

### ADR-0021: Pinned, hash-verified models; text-line orientation model for English only (2026-10-22)
`models.lock.json` pins 14 models (8 PaddleOCR, 6 `tessdata_best`) by source revision and
per-file SHA-256. They are fetched only at staging (`make models`) with independent hash
cross-checks, and verified again at runtime before first use. The owner accepted on
2026-10-22 that vendors do not disclose training data (weights are Apache-2.0).
**Finding:** the PP-LCNet text-line orientation classifier misreads upright Indic text as
upside down and flips it. It flagged every upright Devanagari line (Hindi CER 0.897 with it,
0.007 without; Marathi 0.887 vs 0.007) and 9 of 15 lines on a Tamil serif scan (CER 0.707;
0.009 without). It is therefore used **only for English**, where it was validated: CER at or
below 0.001 on every English corpus page, and upside-down pages detected and ordered
correctly (`orientation_deg` = 180). Page orientation for Indic-script pages is not detected
yet (OD-18), so upside-down Indic pages read poorly. Preprocessing still corrects 90°
rotations for every script (ADR-0016).

### ADR-0022: One engine interface; primary + fallback; compare, never merge (2026-10-22)
All engines implement `OcrEngine` (`newgen_ocr/ocr/engine.py`) and return the same schema:
lines with text, 0..1 confidence, pixel bbox (top-left origin) and an optional polygon,
words and orientation. Each run records the engine version and the exact models
(revision + file hashes). Policy:
- Native-text PDF pages and blank pages are not OCRed.
- OCR reads the requested profile's preprocessed image when one exists, else the source page
  (`image_source`).
- `auto`: primary engine (PaddleOCR) per page; the fallback (Tesseract) is used only when the
  primary is unavailable or fails on that page, recorded as `fallback_from`/`fallback_reason`.
  A named engine never falls back.
- `compare`: also runs the secondary engine and stores its run. The per-page agreement
  (1 − CER between the two engines' texts) below `NGOCR_OCR_DISAGREEMENT_THRESHOLD` (0.85)
  sets `needs_review`.
- **Results are never merged or voted** at character, word or line level. The result of
  record is always a single engine's output for a page. A merge policy can be added later
  only if it is defined and measured.

- **Per-language primary** (`NGOCR_OCR_LANGUAGE_PRIMARY`, default `{"kn": "tesseract"}`):
  for listed languages the engines swap roles (primary and fallback). Kannada is routed to
  Tesseract because PaddleOCR's only Kannada model measured CER 0.388 against Tesseract's
  0.013.
- Engine output normalisation is limited to removing invisible word-final joiners
  (Tesseract/Tamil). No other text rewriting happens at the engine layer.

OCR runs synchronously in the request for now (ADR-0012).

## Open decisions (defaults in force until decided)

| ID | Question | Default assumption |
|---|---|---|
| OD-1 | Production container base image | `ubuntu:24.04` + uv-provisioned Python 3.11, for parity with the target OS |
| OD-2 | Queue broker | Valkey (BSD-3-Clause) |
| OD-3 | Handwriting model | None selected. Candidates are license-reviewed in the handwriting phase; **stop and report** if none is clearly licensed |
| OD-4 | PaddleOCR coverage for te/hi/ta/kn/mr on CPU | **Measured on the synthetic corpus (Phase 4)**: see ACCURACY_EVALUATION.md. Real-scan accuracy remains unmeasured |
| OD-5 | Container/image vulnerability scanner | Trivy (Apache-2.0); Grype as an alternative. Needs approval before adoption (Phase 5) |
| OD-6 | API authentication | API keys (hashed in DB) for system integrations, plus session login for the review UI; OIDC optional later |
| OD-7 | Test-client transport | Starlette warns that `httpx` is deprecated in favour of `httpx2`. Migration is held for approval (new dependency) |
| OD-8 | GitHub Actions pinning | Pinned to major tags today; pin to commit SHAs before the first protected-branch merge |
| OD-9 | Untracked empty `requirements.txt` | Left untouched; superseded by `requirements.in`/`.lock`. Propose deleting it |
| OD-10 | Git remote is a placeholder (`YOUR_USERNAME`) | No pushes or commits until the owner authorizes them and sets the remote |
| OD-11 | Dev host is Ubuntu 26.04, target is 24.04 | CI runs on `ubuntu-24.04`; containers will use a 24.04 base (OD-1) |
| OD-12 | Parser isolation | PDFium and Pillow parse untrusted files in the API process. Default: move parsing into the worker in Phase 5 with resource limits (memory, CPU time, no network); evaluate a separate sandboxed subprocess per document |
| OD-13 | Retention of rejected uploads | Deleted immediately; only the audit event (sha256, size, code) remains. Alternative: a quarantine hold for security review |
| OD-14 | Rotated PDF pages | `rotation` is recorded. Text bounding boxes are in unrotated page space with a top-left origin, while rendered images follow PDFium's display orientation. Reconcile in Phase 6 (layout) |
| OD-15 | Upload spooling | Starlette spools multipart files over 1 MiB to `TMPDIR` before the handler runs (size-capped by the body limit). Default: set `TMPDIR` to a directory on the protected data volume in deployment |
| OD-16 | Storage growth and retention | About 4–6 MB per scanned page at 300 DPI (about 40–60 GB/day at 10k pages/day). Default: none yet. Propose a retention period per tree (derived artefacts can be regenerated from originals) before Phase 10 |
| OD-17 | Per-language engine choice | **Decided 2026-10-22 on measurement:** Kannada → Tesseract-primary (PaddleOCR CER 0.388 vs Tesseract 0.013). Others PaddleOCR-primary (see OD-21) |
| OD-18 | Page-level 180° orientation for Indic scripts | Not detected (the text-line classifier is English-only, ADR-0021). Evaluate PaddleOCR's document-orientation classifier (`PP-LCNet_x1_0_doc_ori`) or Tesseract OSD |
| OD-19 | OCR throughput vs 10k pages/day | About 6–7 s/page for PaddleOCR on a 2-core laptop (ACCURACY_EVALUATION.md). 10k pages/day is about 19 core-hours/day, so it must be measured on the reference server before sizing (Phase 10) |
| OD-20 | Mixed-language pages | PaddleOCR recognises one script per call (first requested language); Tesseract accepts several (e.g. `hi+eng`). Default: first language decides the Paddle model; document-level language detection in the classification phase |
| OD-21 | Should more languages default to Tesseract? | On the synthetic corpus Tesseract equals or beats PaddleOCR for every language and is about 3x faster, but the corpus favours Tesseract (clean font renders). Default: keep PaddleOCR primary except Kannada until a real-document evaluation set is measured; then decide per language |
