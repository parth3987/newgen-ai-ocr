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
dependencies needed to accept work (currently `data_dir`; DB and broker are added in Phase 8)
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
Until the job queue exists (Phase 8), `POST /api/v1/documents` ingests synchronously in
FastAPI's thread pool. PDFium is not thread-safe, so all PDFium use is serialised by
`PDFIUM_LOCK`. Throughput is therefore limited to one PDF at a time per process, which is
acceptable for development and must not be used for production volumes.

### ADR-0013: Filesystem document store and manifests as the metadata record (2026-10-22)
Until PostgreSQL arrives (Phase 8), `derived/<id>/manifest.json` is the system of record for
document metadata. Originals (`originals/`, mode 0440) are stored separately from derived
artefacts (`derived/`). Derived output is built in `staging/` and renamed into place
atomically, with the manifest included. Client filenames never touch the filesystem. Document
ids are random UUID4 hex strings. Duplicate uploads become separate documents (sha256 is
recorded so they can be deduplicated later).

### ADR-0014: Append-only JSONL audit trail (2026-10-22)
`audit/audit.jsonl` records `document.accepted` and `document.rejected` with request id,
actor (`anonymous` until auth exists), sha256, size, format, page counts and error code,
never filenames or content. Each line is appended with `O_APPEND` and fsync. It moves to a
database table in Phase 8, with the file kept as a tamper-evident export.

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
`FFMPEG: YES` (`newgen_ocr/imaging/runtime.py`, tested). The build itself is a Phase 8/10 task.

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

### ADR-0023: Language matrix, script identification and review routing (2026-10-23)
**Matrix:** `newgen_ocr/ocr/languages.py` (served at `/api/v1/languages`, documented in
LANGUAGE_SUPPORT.md) enables an engine for a language only with pinned models and measured
results. Kannada is Tesseract-only.

**Script identification** (`languages: ["auto"]`) combines two measured signals:
- *Tesseract OSD* for orientation (clockwise `Rotate`, applied when confidence ≥ 2.0) and for
  naming an unsupported script. OSD's script answer is **not** trusted for supported scripts:
  on Devanagari it depended on text size (Marathi at 150 DPI read as "Cyrillic"; confidence
  0.4–47 across DPI), and rescaling did not fix it.
- A *recognition census*: one Tesseract pass with `eng+hin+tel+tam+kan` on the upright page
  (downscaled to about 150 DPI text height), then a Unicode script count of the output.
  Detected when the top script has at least 60% of characters, or the top two (one of them
  Latin) cover at least 85%, **and** mean confidence ≥ 0.70. Unsupported scripts produce
  scattered output at about 0.45 confidence and are never routed to a supported recogniser.

Measured (ACCURACY_EVALUATION.md): script + orientation 92/96 corpus pages (the 4 misses are
sideways Marathi scans OSD could not orient; they become `script_uncertain`, not misrouted);
0/4 unsupported scripts misrouted; Hindi/Marathi identification 24/24; mixed pages 10/10
routed with CER ≤ 0.009.

**Routing:** for a page with an Indian script plus Latin (either dominant), the Indian-script
recogniser goes first, because Indian-script recognisers include Latin and the English
recogniser cannot read Indian scripts. Two different Indian scripts are flagged `mixed_script`.
For explicit languages, OSD is used for orientation only (`NGOCR_OCR_DETECT_ORIENTATION`),
and script mismatch is judged from the engine's own output. Cost on the laptop: about 4 s/page
for auto (OSD + census), about 1 s for orientation only.

**Review routing:** nothing is guessed. Pages that cannot be read reliably get status
`unsupported` (not OCRed) or `review.needs_review` with reasons (LANGUAGE_SUPPORT.md). The
confidence thresholds (page 0.85, line 0.70) were set from the corpus. Confidence alone does
not catch a systematically wrong recogniser (PaddleOCR Kannada: CER 0.39 at confidence 0.94),
which is why enablement is by measurement.

### ADR-0024: Handwriting is experimental for English and unsupported for Indian scripts (2026-10-23)
Decided by the owner after licence review. No openly licensed Indian-script handwriting model
has clean training-data provenance: the candidates are trained on IIIT research-only datasets
or disclose no data (MODEL_INVENTORY.md lists the rejects). Indian-script handwriting pages
are therefore returned `unsupported` with `handwriting_unsupported`. English handwriting uses
PaddleOCR `PP-OCRv5_server_rec` (Apache-2.0; same stack and lockdown, no new dependencies)
through the `paddle_handwriting` engine (`handwriting: true` on the OCR request). It is
**experimental**: every result carries `handwriting_unverified` and is routed to review until
a language is measured on real samples and listed in `NGOCR_OCR_HANDWRITING_VERIFIED_LANGUAGES`
(empty by default). **Measured on synthetic handwriting fonts** (a weak proxy): CER 0.023 for
the handwriting recogniser against 0.005 for the printed recogniser. On some inputs it drops
English word spaces (a Chinese-centric model), so it is not an improvement on this proxy.
Real-handwriting performance is unknown (OD-23).

### ADR-0025: Layout and tables from PaddleOCR layout models, verified by page evidence (2026-10-23)
Components approved by the owner on 2026-10-23 after a CPU measurement on a probe page:
`PP-DocLayout_plus-L` (default; `PP-DocLayout-M` selectable), `PP-LCNet_x1_0_table_cls` and
`SLANet_plus`, loaded through the same offline lockdown (ADR-0020) and hash-verified store.
Layout is a separate stage that reads a stored OCR run (`POST .../layout`), so it works with
every engine (including Tesseract for Kannada) and its coordinates are those of the OCR image.
Design rules:
1. **No OCR line is altered or dropped.** Each line belongs to the region covering at least
   half of it (tables first; the smaller region on a tie); lines outside every region become
   `unclassified` regions and route the page to review.
2. **Table structure is never repaired by guessing.** SLANet's HTML tokens are parsed into a
   grid; a non-rectangular grid, overlapping spans or a cell/box count mismatch makes the
   table `unsupported` (no cells; text kept in the region).
3. **Wired tables are cross-checked deterministically**: ruling lines are traced into closed
   cells (OpenCV morphology) and must give exactly the model's rows, columns and spans. When
   they do (`verified`), the traced cell interiors replace SLANet's approximate boxes for
   text assignment; a disagreement routes the table to review.
4. **Wireless tables are checked against the OCR lines**: two lines side by side with a wide
   gap in one cell, or a cell boundary cutting through ink inside a line, mean a wrong column
   split (`table_structure_mismatch`).
5. A page line spanning cells, and a cell the page OCR left empty, are re-read from the cell
   crop with the same engine and languages (`text_source: cell_ocr`); a cell that still has
   no text but holds marks routes the table to review (`table_cell_unread`).
6. **Reading order** is a recursive XY-cut (vertical cuts first; aligned bands of multi-line
   blocks kept together), with headers first and footers / page numbers last.
7. **Key-value pairs** only from explicit evidence: "Key: value" on one line, or a two-column
   table row. No word coordinates are estimated.
8. Formulas, charts and seals are recorded as `unsupported` structures; text inside a figure
   is returned without structure.
Measured on the synthetic layout set (ACCURACY_EVALUATION.md): 24 of 26 tables detected, 23
with exact structure, **0 wrong structures returned without review**. Not supported yet:
native-text PDF pages (OD-25), tables without any rules (detected as text, OD-26), tables
continuing across pages, nested tables, rotated tables.

**Addendum to ADR-0020 (2026-10-23): one Paddle thread per process.** With oneDNN, Paddle
predictors used from several OS threads failed intermittently (`RuntimeError:
std::exception`) and once returned degraded OCR (4 lines instead of 16) without error;
measured: OCR from a new thread per call failed on 2 of 6 pages, never on one thread, never
without oneDNN. FastAPI runs sync endpoints on a thread pool, so this affected Phases 4-5 as
well. All Paddle model creation and inference now runs on a single dedicated thread
(`paddle_runtime.run_on_paddle_thread`); calls were already serialised, so throughput is
unchanged. Regression test: `test_paddle_calls_from_many_threads`.

### ADR-0026: Structured extraction by rules, templates and checks; no LLM (2026-10-23)
Extraction runs on a stored layout run and needs no model: rule- and template-based
classification, configurable JSON field schemas, label, section, table and format
(regex/checksum) candidates, normalisers, Pydantic validation and consistency checks
(EXTRACTION.md). The owner chose the first document types on 2026-10-23: GST tax invoice,
receipt / cash memo, bank statement, PAN card, Aadhaar card. Principles:
1. **Missing stays missing.** No value is inferred from position or likelihood. A value
   whose field cannot be decided (e.g. a GSTIN outside any seller/buyer section) is
   `ambiguous`; disagreeing candidates are a `conflict`; both keep `value: null`.
2. **Evidence on every value:** page, source, OCR line indices, box, confidence.
3. **Checks never assume zero** for a missing input, except declared optional terms
   (round-off; the message says when they were counted as 0).
4. **Personal data:** Aadhaar masked to the last 4 digits by default in results and exports
   (UIDAI guidance); identity fields marked `sensitive`.
5. **Exports** (JSON, CSV, XLSX) neutralise spreadsheet formula injection.
Excel export uses **openpyxl 3.1.5** (+ et-xmlfile 2.0.0), both MIT, pure Python, admitted
under ADR-0007 (owner-approved 2026-10-23; age, hash, audit and licence checks pass). Only
those two packages were added (the lock was regenerated without `--upgrade`).
**Measured on a synthetic set** (ACCURACY_EVALUATION.md): 0 invented and 0 wrong values in
248 field judgements; on a held-out invoice design, 6 of 12 values were found before
generic fixes and 9 of 12 after, again 0 wrong and 0 invented.

## Open decisions (defaults in force until decided)

| ID | Question | Default assumption |
|---|---|---|
| OD-1 | Production container base image | `ubuntu:24.04` + uv-provisioned Python 3.11, for parity with the target OS |
| OD-2 | Queue broker | Valkey (BSD-3-Clause) |
| OD-3 | Handwriting model | None selected. Candidates are license-reviewed in the handwriting phase; **stop and report** if none is clearly licensed |
| OD-4 | PaddleOCR coverage for te/hi/ta/kn/mr on CPU | **Measured on the synthetic corpus (Phase 4)**: see ACCURACY_EVALUATION.md. Real-scan accuracy remains unmeasured |
| OD-5 | Container/image vulnerability scanner | Trivy (Apache-2.0); Grype as an alternative. Needs approval before adoption (Phase 8) |
| OD-6 | API authentication | API keys (hashed in DB) for system integrations, plus session login for the review UI; OIDC optional later |
| OD-7 | Test-client transport | Starlette warns that `httpx` is deprecated in favour of `httpx2`. Migration is held for approval (new dependency) |
| OD-8 | GitHub Actions pinning | Pinned to major tags today; pin to commit SHAs before the first protected-branch merge |
| OD-9 | Untracked empty `requirements.txt` | Left untouched; superseded by `requirements.in`/`.lock`. Propose deleting it |
| OD-10 | Git remote is a placeholder (`YOUR_USERNAME`) | No pushes or commits until the owner authorizes them and sets the remote |
| OD-11 | Dev host is Ubuntu 26.04, target is 24.04 | CI runs on `ubuntu-24.04`; containers will use a 24.04 base (OD-1) |
| OD-12 | Parser isolation | PDFium and Pillow parse untrusted files in the API process. Default: move parsing into the worker in Phase 8 with resource limits (memory, CPU time, no network); evaluate a separate sandboxed subprocess per document |
| OD-13 | Retention of rejected uploads | Deleted immediately; only the audit event (sha256, size, code) remains. Alternative: a quarantine hold for security review |
| OD-14 | Rotated PDF pages | `rotation` is recorded. Text bounding boxes are in unrotated page space with a top-left origin, while rendered images follow PDFium's display orientation. Layout (Phase 6) works in OCR-image coordinates, so it is unaffected; reconcile together with OD-25 |
| OD-15 | Upload spooling | Starlette spools multipart files over 1 MiB to `TMPDIR` before the handler runs (size-capped by the body limit). Default: set `TMPDIR` to a directory on the protected data volume in deployment |
| OD-16 | Storage growth and retention | About 4–6 MB per scanned page at 300 DPI (about 40–60 GB/day at 10k pages/day). Default: none yet. Propose a retention period per tree (derived artefacts can be regenerated from originals) before Phase 10 |
| OD-17 | Per-language engine choice | **Decided 2026-10-22 on measurement:** Kannada → Tesseract-primary (PaddleOCR CER 0.388 vs Tesseract 0.013). Others PaddleOCR-primary (see OD-21) |
| OD-18 | Page-level 180° orientation for Indic scripts | **Resolved 2026-10-23 (ADR-0023):** Tesseract OSD orientation is applied before OCR for every script; measured correct on all upright/180° corpus pages. Evaluate PaddleOCR's document-orientation classifier (`PP-LCNet_x1_0_doc_ori`) or Tesseract OSD |
| OD-19 | OCR throughput vs 10k pages/day | About 6–7 s/page for PaddleOCR on a 2-core laptop (ACCURACY_EVALUATION.md). 10k pages/day is about 19 core-hours/day, so it must be measured on the reference server before sizing (Phase 10) |
| OD-20 | Mixed-language pages | **Resolved 2026-10-23 (ADR-0023):** the census detects secondary scripts; Indian script + English pages use the Indian-script recogniser (reads Latin); two Indian scripts on a page are flagged for review |
| OD-21 | Should more languages default to Tesseract? | On the synthetic corpus Tesseract equals or beats PaddleOCR for every language and is about 3x faster, but the corpus favours Tesseract (clean font renders). Default: keep PaddleOCR primary except Kannada until a real-document evaluation set is measured; then decide per language |
| OD-22 | Better English handwriting recogniser | `PP-OCRv5_server_rec` underperformed the printed model on the synthetic proxy and drops word spaces. Candidates to evaluate on real samples: `PP-OCRv6_medium_rec` (Apache-2.0, claims handwritten English). TrOCR remains excluded (IAM non-commercial training data) |
| OD-23 | Real handwriting evaluation set | Owner to supply consented samples (`evaluation/handwriting/README.md`; at least 30 pages from 5 writers per language). Until then, handwriting stays experimental (English) or unsupported (Indian scripts) |
| OD-24 | Sideways pages in auto mode without preprocessing | OSD could not orient 4 sideways Marathi scans, which become `script_uncertain`. The default `standard` preprocessing turns 90° pages upright first; consider trying the census on 90° rotations when OSD is undetermined |
| OD-25 | Layout of native-text PDF pages | Not OCRed, so they have no page image or OCR lines: layout returns them `skipped: native_text`. Proposal: render them for layout and use the embedded text segments as lines (needs OD-14) |
| OD-26 | Tables without any rules | Not detected as tables by PP-DocLayout on the test set (2 of 2 missed); their text is returned as text regions in row order and the page is routed to review. Candidate: a whitespace-alignment detector, or a real-document test of `PP-DocLayout-L` |
| OD-27 | Real-document layout and table test set | Synthetic only (ACCURACY_EVALUATION.md). Default: build one from consented real documents (invoices, statements, forms) with cell-level ground truth before relying on table output in production; PubTabNet/FinTabNet (CDLA-Permissive) are options for printed English tables |
| OD-28 | Real-document extraction set | Synthetic only (ACCURACY_EVALUATION.md). Default: collect consented real invoices, receipts, statements and ID scans (with masking) and measure field precision/recall per type before production use |
| OD-29 | Parties without section headings | Letterhead seller details (name, address) with no "Seller"/"From" heading are not extracted (held-out invoice). Options: per-vendor templates, or a declared "letterhead = seller" rule per schema |
| OD-30 | Aadhaar numbers in OCR/layout artefacts | Extraction masks them; OCR and layout runs keep the page text verbatim. Options: redact Aadhaar patterns in stored OCR text, or restrict and expire derived artefacts (with OD-16) |
| OD-31 | Unruled bank statements | Transaction tables without rules are not detected by layout (OD-26), so their transactions are not extracted (the statement is routed to review). Candidate: line-based transaction parsing with column positions from the header line |
| OD-32 | Amounts in words | "Rupees Eleven Thousand Eight Hundred Only" is not parsed or cross-checked against the total. Candidate: an Indian-numbering words parser as an extra check |

