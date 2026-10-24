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
13.0.0) and `certifi` (dev only). Remove each override once the floor passes it. Added 2026-10-24 (owner-approved): `filelock` 4.2.2 (CVE-2026-112940/112913/112907) and `setuptools` 84.2.0 (GHSA-xmj3-9gf7-h52w). None is reachable in this deployment (Windows-only, or the unused `ReadWriteLock`), but `pip-audit --strict` is kept clean. The nginx 1.30.6 image is admitted the same way (ADR-0031). Also owner-approved 2026-10-24: npm `undici` 8.12.2 (GHSA-xjj6-f33q-929c and two others), dev-only (pulled in by jsdom for tests), pinned with an `overrides` entry in `frontend/package.json`; remove the entry once 8.12.2 is 14 days old (2026-10-29).
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
dependencies needed to accept work (currently `data_dir`; DB and broker are added in Phase 9)
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
Until the job queue exists (Phase 9), `POST /api/v1/documents` ingests synchronously in
FastAPI's thread pool. PDFium is not thread-safe, so all PDFium use is serialised by
`PDFIUM_LOCK`. Throughput is therefore limited to one PDF at a time per process, which is
acceptable for development and must not be used for production volumes.

### ADR-0013: Filesystem document store and manifests as the metadata record (2026-10-22)
Until PostgreSQL arrives (Phase 9), `derived/<id>/manifest.json` is the system of record for
document metadata. Originals (`originals/`, mode 0440) are stored separately from derived
artefacts (`derived/`). Derived output is built in `staging/` and renamed into place
atomically, with the manifest included. Client filenames never touch the filesystem. Document
ids are random UUID4 hex strings. Duplicate uploads become separate documents (sha256 is
recorded so they can be deduplicated later).

### ADR-0014: Append-only JSONL audit trail (2026-10-22)
`audit/audit.jsonl` records `document.accepted` and `document.rejected` with request id,
actor (`anonymous` until auth exists), sha256, size, format, page counts and error code,
never filenames or content. Each line is appended with `O_APPEND` and fsync. It moves to a
database table in Phase 9, with the file kept as a tamper-evident export.

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
`FFMPEG: YES` (`newgen_ocr/imaging/runtime.py`, tested). The build itself is a Phase 9/11 task.

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

### ADR-0027: Local bearer tokens with roles for review (2026-10-23)
The owner chose local tokens over a trusted proxy header or authenticating every endpoint in
this phase. Design:
- **Tokens** are `ngocr_<id>_<secret>` with a 256-bit secret from `secrets`. Only its
  SHA-256 is stored, in a JSON file (`NGOCR_AUTH_TOKENS_FILE`, mode 0600). A fast hash is
  sufficient for random secrets of this length, and comparison is constant time.
- **Management** is with `python -m newgen_ocr.auth create|list|revoke`. Tokens may expire,
  and the file is re-read on change, so revocation needs no restart.
- **Roles** are ordered `viewer < reviewer < approver < admin` and checked per endpoint.
- **Audit:** failures record the reason and the token's public id, never the token. Review
  actions record the authenticated user as `actor`.
- **Scope:** only the review endpoints are authenticated now. The rest follow in the
  persistence phase (OD-6), with database-backed keys and a session login for the full
  review application.
- **No new dependency.**

### ADR-0028: Calibrated verification gates and a human review queue (2026-10-23)
Automatic acceptance is never based on OCR confidence alone (REVIEW.md):
- **Gates.** A value is verified only when it passes all of these:
  - it was extracted;
  - the document type is reliable;
  - its source page is clean;
  - no check involving it failed;
  - the second OCR engine does not read a different value there;
  - its confidence reaches a threshold calibrated **for that field** on held-out ground
    truth (one-sided 95% Wilson lower bound of precision ≥ 0.95 over ≥ 50 values).
  Fields without enough data use their type's pooled threshold only when their own data
  shows no error above it; otherwise they have no threshold and always go to a person.
- **Calibration file.** Thresholds live in a calibration file whose id is a hash of its
  content, bound to schema versions, with its dataset kind recorded.
- **Automatic acceptance** is off by default, and the owner chose to keep it so until
  thresholds are fitted on real documents. Production refuses a synthetic calibration
  unless explicitly allowed.
- **Review queue, owner's choices of 2026-10-23.**
  - Storage: the local file store, with the history as an append-only, hash-chained JSON
    Lines file, under an inter-process `flock`.
  - Page: a minimal built-in review page with no external assets.
  - Corrections are normalised and validated like extracted values, need reason codes,
    and use optimistic concurrency.
  - Approval requires a second person (configurable) and explicit acknowledgement of
    checks that still fail.
- **Engine cross-check.** A field-level comparison with the second engine's words replaces
  the page-level text comparison for verification. The page-level comparison stays in the
  OCR result.

### ADR-0029: PostgreSQL state, Celery jobs on Valkey, authentication everywhere (2026-10-23)
Phase 9 adds a **service mode**, used when `NGOCR_DATABASE_URL` and `NGOCR_BROKER_URL` are
both set. Staging and production refuse to start without them. Local mode (neither set)
keeps the synchronous API and file stores for development only.
- **What is stored where (owner's choice).**
  - PostgreSQL 17 holds tokens, documents and pages, jobs and job events, extraction results
    (JSONB), review items and revisions, the review history (hash chain kept) and audit
    events.
  - Originals, page images and OCR/layout/extraction JSON stay on the shared data volume.
  - The review history and audit tables are append-only, enforced by a database trigger.
    This closes OD-34 for anyone without database superuser rights.
- **Driver.** The owner chose plain psycopg 3 (LGPL-3.0, used unmodified as a library) with
  the operating system's libpq (Ubuntu `libpq5`, security-patched by the OS).
  `psycopg-binary` was rejected because it bundles end-of-life CentOS 7 libraries.
- **New dependencies.** SQLAlchemy 2, Alembic and Celery 5 with redis-py: 18 packages in
  all, each licence-reviewed (THIRD_PARTY_NOTICES.md). The schema is created and changed
  only through migrations: `python -m newgen_ocr.db upgrade`.
- **Jobs.**
  - The broker carries only job ids. State lives in PostgreSQL, so losing the broker loses
    no work.
  - The API checks an upload cheaply (size, signature, extension). It then records the
    document and its job in one transaction, publishes the id, and returns `202`.
  - Workers acknowledge a message only when the task ends, and Celery requeues it when a
    worker process dies.
  - Only an atomic claim (a conditional `UPDATE`) starts an attempt, so duplicate messages
    are harmless.
  - A running job holds a lease renewed by a heartbeat. Every write after the claim checks
    that this worker still holds it.
  - The reaper requeues jobs whose lease expired, failing them with `WORKER_LOST` after the
    last attempt. It also re-sends queued jobs whose message was lost, but only when the
    broker queue is empty.
  - Stages are idempotent (each replaces its own output atomically), and completed stages
    are skipped on retry.
  - Deterministic errors fail a job at once. Unexpected errors retry with backoff, up to
    `NGOCR_JOB_MAX_ATTEMPTS` attempts.
  - Cancellation takes effect between stages.
- **Duplicates.**
  - An `Idempotency-Key` header replays the original job. Reusing the key for a different
    request returns `409`.
  - The same file from the same user returns the earlier job unless `force` is set.
  - A partial unique index allows only one active job per document.
- **Bounded work.** Uploads are refused when the queue is full (`503 QUEUE_FULL`) or when a
  user has too many active jobs (`429 TOO_MANY_JOBS`). Each worker runs
  `NGOCR_WORKER_CONCURRENCY` processes and prefetches one message.
- **Access (owner's choice).**
  - Every endpoint except the health probes and the static review page needs a bearer
    token.
  - Tokens are stored in PostgreSQL in service mode.
  - A new `submitter` role, below viewer, sees and controls only its own documents and
    jobs. Documents it may not see are reported as not found.
  - Viewers and above see everything. Only admins control other users' jobs.
  - Lists are paginated (`limit` up to 200, `offset`).
- **Private ports.** `deploy/compose.yaml` puts PostgreSQL and Valkey on an internal
  network with no published ports. Passwords are read from secret files: SCRAM for
  PostgreSQL, and a config file rather than the command line for Valkey.
  `deploy/compose.dev.yaml` publishes them on 127.0.0.1 for development and tests.
- **Logs.**
  - The engine uses `hide_parameters`, so bound values never appear in errors.
  - URLs are `SecretStr`; startup logs show only host, port and database name.
  - Job errors store the error code's public message, never exception text.
  - Workers log job ids, stage names and codes only.

### ADR-0030: Web application with password sign-in and server-side sessions (2026-10-23)
Phase 10 adds the React web application at `/app`. It replaces the Phase 8 `/review` page,
which now redirects there. The owner's choices:
- **Sign-in.** Local user accounts with passwords, and server-side sessions. API tokens
  remain for integrations.
  - **Hashing:** passwords are hashed with the standard library's scrypt (n = 2^15, r = 8,
    p = 1, random salt), so there is no new dependency. Passwords must be 12 to 256
    characters and must not contain the user name.
  - **Lockout:** after `NGOCR_LOGIN_MAX_FAILURES` wrong passwords (default 5) the account
    locks for `NGOCR_LOGIN_LOCKOUT_MINUTES` (default 15). Refused sign-ins all get the same
    response, and an unknown name costs the same hash time. The audit log records the real
    reason.
  - **Cookie:** the session value is 256 random bits in a cookie that is `HttpOnly`,
    `SameSite=Strict` and `Secure` outside development (production refuses `Secure` off).
    Only its SHA-256 is stored.
  - **Expiry:** sessions end after `NGOCR_SESSION_IDLE_MINUTES` of inactivity (default
    30), and in any case after `NGOCR_SESSION_MAX_HOURS` (default 12).
  - **Changes apply at once:** the user's current role and status are read on every
    request. Disabling a user, changing their role or setting a new password ends their
    sessions.
  - **CSRF:** every cookie request that changes something must carry the CSRF token
    (`X-CSRF-Token`). It is the SHA-256 of the session value, so it needs no storage and
    cannot be computed without the cookie. Bearer requests need no token.
  - This closes OD-35.
- **Frontend dependencies.** A minimal React stack.
  - Shipped (6 packages, all MIT): React 19.3, React DOM, React Router 8.4 and their
    dependencies.
  - Build and test only, not shipped: TypeScript 7, Vite 8 and Vitest 5 with Testing
    Library and jsdom.
  - There is no UI component library. The CSS is hand-written and uses system fonts.
  - All 141 npm packages are pinned in `package-lock.json` with sha512 integrity hashes and
    were resolved with `--before` (at least 14 days old). Install scripts never run
    (`ignore-scripts`). They are licence-checked (`scripts/check_npm_licenses.py`) and
    audited (`npm audit`) in the gate.
- **Shipping.** `make frontend` builds at staging into `newgen_ocr/webapp/dist`, which goes
  into the Python package. The build output is not committed, and production never runs
  Node. FastAPI serves the app from the same origin with a strict CSP: `default-src
  'none'`, and scripts, styles, images and API calls from `'self'` only. The browser itself
  therefore refuses any CDN, analytics or other external request.
- **Access stays in the API.** The app only hides what a role cannot use. Every rule is
  enforced by the API, and tests call it directly as each role.
- **New endpoints:**
  - sign-in: login, session, logout, password change;
  - extraction search: `GET /api/v1/extractions`;
  - document search: filename or id, submitter, dates;
  - admin: overview, audit log, users.
  Admin and search need service mode, like jobs.

### ADR-0031: Hardened containers, TLS ingress, egress isolation, backups and retention (2026-10-24)
Phase 11. The owner's choices: commit Phase 10 first; nginx for TLS; Trivy for image
scanning; the minimal OpenCV built from source in the image.
- **Image** (`deploy/Dockerfile`). One image serves the API, the workers and one-shot
  commands. It is built at staging; the result needs no network.
  - **Base:** `ubuntu:noble-20260917` by digest (closes OD-1). Python 3.11 comes from uv
  0.12.24 (pinned by digest), which checks the download against its own manifest.
  - **Python packages:** installed with `--require-hashes` from `requirements.lock`. The
    app itself is installed without build isolation, using the locked setuptools, so
    nothing unpinned is fetched.
  - **Models:** `models/` is verified with `fetch_models.py --verify`, which also refuses
    any file the lock does not list. So only approved models ship.
  - **Runtime packages:** `tesseract-ocr`, `libpq5` and `libgomp1`, nothing else.
  - **User and permissions:** the service runs as uid 10001. `/opt` is read-only,
    `NGOCR_ENVIRONMENT=production`, and temporary uploads go to `/data/tmp` (mode 0700;
    OD-15).
- **Minimal OpenCV** (ADR-0015, as implemented).
  - **Source:** the `opencv-python-headless` 4.14.0.94 source distribution, checked
    against the SHA-256 that PyPI publishes.
  - **Modules:** core, imgproc, imgcodecs and photo. imgcodecs is added to ADR-0015's list:
    our code and PaddleX decode and encode images with it. PNG and JPEG are built from
    OpenCV's bundled sources.
  - **Left out:** videoio and FFmpeg, GUI, OpenSSL, IPP, OpenCL, TIFF, WebP and OpenEXR.
    Nothing is downloaded during the CMake build.
  - **Linking and packaging:** static, packaged as `opencv-python-headless
    4.14.0.94+ngocr.minimal`. That satisfies the lock's `==4.14.0.94` and replaces the PyPI
    wheel in the image. Its SHA-256 is recorded in the image (`/opt/ngocr/opencv-wheel.sha256`).
  - **Check:** the production guard runs at build time and at every start.
- **Compose** (`deploy/compose.yaml`, profile `app`): migrations, then the API, the workers
  and the nginx ingress.
  - **Networks:** `backend` (database, queue, API, workers) and `edge` (ingress to API) are
    internal. Only the ingress joins a routable network, to publish HTTPS.
  - **Hardening on every service:** `no-new-privileges`, all capabilities dropped (a few
    added back for the PostgreSQL entrypoint, and SETUID and SETGID for nginx's workers),
    read-only root filesystems with small tmpfs mounts, and limits on memory, CPU and
    processes.
  - **Secrets** are files under `deploy/secrets/` (directory 0700), mounted only into the
    services that need them. The application reads them through `NGOCR_SECRETS_DIR`, so no
    credential is an environment variable.
- **TLS** (`deploy/nginx/nginx.conf`).
  - **Protocols and ciphers:** TLS 1.2 and 1.3 with Mozilla-intermediate ciphers, session
    tickets off, HSTS.
  - **Limits:** a 51 MiB body cap (the API enforces the exact limit), timeouts, and
    per-address limits on requests, connections and sign-in. This closes the proxy part of
    OD-36.
  - **Headers:** `X-Forwarded-For` is replaced, never appended.
  - **Logs:** the path only, never the query string.
  - **Certificates:** production uses certificates from the organisation's CA.
    `make certs` creates a local CA for development and the offline test.
- **nginx 1.30.6 is admitted under ADR-0007's security override.** It was released
  2026-10-14, 10 days before this decision, and fixes five CVEs, among them worker buffer
  overflows (CVE-2026-15176, CVE-2026-93479). The override lapses when the age floor
  passes it (2026-10-28); remove the note then.
- **Egress.**
  - **Layer 1:** internal container networks, verified by `make offline-test`. It runs
    the production stack, checks that DNS, TCP, UDP and HTTPS to the internet fail from
    the API and workers, and processes a document end to end over TLS.
  - **Layer 2:** a host nftables template (`deploy/egress/nftables-egress.nft`; the
    syntax is checked, the operator applies it).
- **Backups.** `make backup` writes `pg_dump` plus a tarball of the document volume, a
  manifest and SHA-256 sums. `make restore` checks the sums, replaces both, then runs
  `python -m newgen_ocr.ops verify`. That compares counts and a fingerprint, re-checks the
  review history chain, and checks that every document's files are present. Closes OD-42
  (scheduling and off-site copies are the operator's).
- **Retention.** `python -m newgen_ocr.ops retention` is a dry run by default.
  - **Deletes:** documents older than `NGOCR_RETENTION_DOCUMENT_DAYS` (default unset: kept),
    with their files and rows; events of finished jobs older than 90 days; ended sessions;
    and stale quarantine files.
  - **Keeps:** documents with an active job or an open review item. Audit events and the
    review history are append-only and never deleted (OD-48).
  - **Audit:** each deletion is recorded (`document.deleted`).
- **Scanning.** `make image-scan` runs Trivy 0.75.0 (by digest) on the saved image: OS
  packages, Python packages, secrets and licences. It writes JSON reports and a CycloneDX
  image SBOM, and fails on fixable HIGH or CRITICAL findings. It also scans the repository
  for misconfiguration. Trivy downloads its database at staging only. Closes OD-5.

### ADR-0032: Signed offline installation bundle, ngocrctl, and rollback (2026-10-24)
Phase 12. The owner's choices: commit Phase 11 first; sign with OpenSSH Ed25519; ship no OS
packages and check the host instead.

**Two workflows**, kept apart:
- **Connected staging:** `make staging`.
  - **Fetches and verifies:** the locked Python packages, the verified models and the web
    application.
  - **Runs the gates:** lint, types, licences, pip-audit, npm audit, Gitleaks, the full test
    suite (JUnit report), the image build, Trivy, the SBOMs and the offline network test.
  - **Then runs `make bundle`.**
- **Disconnected production:** `ngocrctl`, a POSIX sh script. It needs only:
  - a container engine with Compose v2;
  - `ssh-keygen`;
  - coreutils.

  It never pulls an image or downloads anything (a test checks the script).

**What the bundle builder (`scripts/build_bundle.py`) checks.** It packages only what the
gates passed. It refuses a bundle unless:
- the Trivy report and the image SBOM name the shipped image's content ID, with no fixable
  HIGH or CRITICAL finding;
- the offline test passed on that same image ID;
- the JUnit report shows tests and no failures or errors;
- the models verify against the lock with nothing unlisted;
- the Python and npm licence checks pass;
- every wheel's hash is in `requirements.lock`, and every locked package has a wheel;
- the minimal OpenCV wheel has the hash recorded inside the image;
- the image's version equals the source's;
- the git tree is clean. `--allow-dirty` is for rehearsals only, and is recorded in the
  bundle and printed at install.

**Bundle contents** (`dist/newgen-ocr-<release>.tar`):
- **Images:** the application image, plus PostgreSQL, Valkey and nginx pulled by digest,
  saved as gzip docker-archives. Each is tagged `localhost/ngocr/<name>:<release>` and
  recorded with its content ID. `config/compose.images.yaml` points every service at those
  tags with `pull_policy: never`.
- **Wheels:** all 97 locked wheels (cp311, manylinux x86-64), with PyPI's OpenCV replaced
  by the minimal build, plus the application wheel and the lock.
- **Models:** the model files and `models.lock.json`.
- **Migrations:** the migration scripts, for review; the image runs them.
- **Configuration:** compose, nginx, the backup and restore scripts, the egress template
  and a site settings template.
- **Licences and SBOMs:** licence notices, the model inventory, and both SBOMs.
- **Evidence:** the Trivy, offline-test and JUnit reports.
- **Documentation:** the offline deployment guide, the runbook, the security review, the
  threat model and the changelog.
- **The production tools:** `ngocrctl`, `INSTALL.md`, and the smoke and probe scripts that
  run inside containers.
- **OS packages:** listed per image under `os-packages/`. The application's OS packages are
  inside its image; nothing is installed on the host.
- **Manifests:** `MANIFEST.json`, a shell-readable `release.env` (release, version,
  schema head, commit) and `images.txt`.

**Integrity.**
- **The bundle's own sums:** `SHA256SUMS` covers every file and is signed with
  `ssh-keygen -Y sign`, namespace `newgen-ocr-bundle`, principal `newgen-ocr-release`.
- **The outer tar:** written deterministically (sorted, fixed owner, modes and times) and
  signed too, so it can be checked before extraction.
- **The site's trusted key:** `newgen-ocr.allowed_signers`. It travels separately, and its
  fingerprint is confirmed by a separate channel. `install` keeps a copy under
  `<prefix>/config/` for later upgrades.
- **What `ngocrctl verify` refuses:**
  - a bad signature;
  - any hash mismatch;
  - any file not listed;
  - any symbolic link.
- **Images:** after loading, each image's content ID must equal the manifest's.
- **Models:** the application image's model lock must equal the bundle's, and every model
  file in the image is re-hashed. This runs in a container with `--network none`.

**Installed layout:** under `<prefix>` (default `/opt/newgen-ocr`, or `NGOCR_PREFIX`).
- `releases/<release>/` holds the verified bundle, read-only.
- `current` is a symlink to the active release, switched atomically.
- `config/` holds `site.env`, the secrets (generated on the host) and TLS. It is written
  on the first install and never overwritten.
- `backups/` holds the backups.
- `state/` holds `previous`, `upgrade-backup`, `history` and a lock.

The compose project name stays `newgen-ocr`, so data volumes carry over between releases.
`NGOCR_SECRETS_PATH` points compose at the site's secrets; a checkout still uses
`deploy/secrets/`.

**Upgrade.**
1. Refuses a release with an older schema head; use rollback for that.
2. Stops the API, workers and ingress, then takes a backup with the old release's tools.
3. Records the backup and the previous release.
4. Switches `current` and starts the new release (migrations first).
5. Runs the smoke tests. On failure it prints the rollback command. It never rolls back
   on its own: restoring loses data, so the operator decides.

**Rollback.**
1. Verifies the target release again and reloads its images if needed.
2. If the two schema heads differ, it must restore. It restores only the backup recorded
   for an upgrade from that exact release.
3. Stops, switches `current`, restores if needed, then starts.
4. Runs `ops verify` (counts, fingerprint, history chain, files) and the smoke tests.

`--no-restore` keeps newer data and is allowed only when the schemas match.

**Smoke tests** (`ngocrctl smoke`, also run by `install`):
- **OCR with no network:** a one-off container with `--network none` loads every model
  the language matrix enables, and reads a generated invoice end to end in local mode.
- **Egress probe:** from the API and worker containers, the same probe as `make
  offline-test`.
- **A job over HTTPS:** a container on the host network connects to the published HTTPS
  address and checks the certificate against the site CA for the configured name. It uses
  a one-day submitter token that is revoked afterwards. The test document stays and is
  named `ngocr-smoke.png`.
- **`--restart`:** first recreates every service. It then checks that the image store is
  unchanged, the models still verify, and the logs show no download attempts.

The smoke scripts use only the standard library and the package's public API, so they
also check older releases after a rollback.

**Rehearsal** (`make rehearse-install`, `scripts/rehearse_install.py`).
- **The simulated host:** a user namespace (`unshare --map-auto`) with:
  - a network namespace with only loopback (no DNS);
  - an empty image store;
  - its own cgroup tree (so limits apply);
  - root Podman with its API socket.
- **The run:** the whole production workflow with the real bundles. That is: outer
  signature, extract, verify, install, `smoke --restart`, an upgrade to a second bundle,
  new data, `rollback --restore`, and a data comparison.
- **Root:** none needed.

**Reproducibility.**
- **Pinned:** every input (images by digest, Python and npm by hash, models by hash).
- **Deterministic:** the bundle tar's bytes depend only on its contents.
- **Not byte-reproducible:** the image is not. The OpenCV build and the image layers carry
  timestamps. This is why the bundle identifies images by content ID and binds the gate
  reports to that ID.

### ADR-0033: Release evaluation, cohorts, targets and release gates (2026-10-24)
Phase 13. The owner's choices:
- performance is measured by a benchmark kit the owner runs on the CPU server;
- synthetic cohorts now, real documents later;
- three 99% targets, provisional until measured on real documents;
- handwriting is measured but not gated on accuracy.

**Cohorts** (`evaluation/cohorts.json`, `newgen_ocr/evaluation/cohorts.py`). Every
document set is a cohort with a **role**, a **kind**, its languages, document types,
conditions and metric families.
- **Roles.** A `tuning` cohort was used to choose engines, write rules, set preprocessing
  or fit review thresholds. An `evaluation` cohort is for final measurement only.
- **Kinds.** `synthetic` cohorts come from a generator with a fixed seed. `real` cohorts
  are a folder under `evaluation/real/`, with `<id>.truth.json` beside each document.
- **The tuning cohorts:** the four existing development sets (the OCR corpus, the
  extraction set, the layout set and the review calibration set, both of its splits).
- **The evaluation cohorts:**
  - clean printed text and clean printed documents;
  - poor scans, of text and of documents;
  - tables;
  - one cohort per printed language (6);
  - mixed language;
  - handwriting: Latin (experimental), Devanagari (unsupported, a routing check), and
    Telugu, Tamil and Kannada (real data only; no offline handwriting font exists);
  - real cohorts for documents, clean printed documents and clean printed text.

**Separation** (the spec's "do not use the same documents for tuning and final
evaluation"). It works in four ways:
- **By file and by text.** Every document is identified by its file's SHA-256 and by a
  hash of its normalised truth text. `scripts/evaluate_release.py` builds the manifests of
  every tuning cohort and refuses to run if any evaluation document matches one, or if two
  evaluation cohorts share a file.
- **By seed.** The registry refuses a generator seed used twice.
- **At the tuning tools.** `scripts/calibrate_review.py --dataset` refuses any folder
  holding an evaluation cohort.
- **In the reports.** Synthetic evaluation cohorts use new seeds, random word sequences
  rather than the tuning sentences, and fonts tuning never rendered. They do share the
  generators' templates and vocabulary, which the reports state as each cohort's
  `independence`. So they test the machinery and catch regressions; only real cohorts are
  evidence about real documents.

**Metrics** (`newgen_ocr/evaluation/release_metrics.py`, one definition each). Every
proportion carries its count and a 95% Wilson interval.

| Metric | Definition |
|---|---|
| CER and WER | Micro-averaged: total edits over total reference characters or words |
| Character accuracy | 1 − CER |
| Field exact match | Fields whose truth has a value, extracted with exactly that value (whitespace and case normalised). Fields with no truth value count separately: correctly missing, or invented |
| Document exact match | The type is right and every field is right |
| Table structure | The exact grid (rows, columns, spans), and the mean adjacency F1 |
| Auto-accept precision | Of the values the review gate verified with the shipped calibration, the share that are correct |
| Coverage | The share of all values verified |
| Document auto-accept rate and manual-review rate | Documents with every value verified, and the rest |
| Per-page latency | p50 and p95 |
| Pages per minute | Pages over the run's wall time |
| CPU seconds per page, peak memory | From the process's resource usage |

The service benchmark adds queue delay (`started_at − created_at`), processing time, the
failure rate, and the containers' CPU and memory.

**Reports** (`reports/evaluation/<cohort>.json` and `summary.json`). Each report has:
- the dataset size, languages, document types and conditions;
- the data kind and its independence;
- the environment (CPU model, cores, memory, OS, version, commit, the model lock's hash,
  the calibration);
- slices by language, condition and document type;
- every document's row.

**Service benchmark** (`ngocrctl benchmark`, `deploy/bundle/benchmark.py` and
`benchmark_report.py`). It runs from the bundle on the installed host:
- **Documents:** the bundle's fixed mix of 18, rendered at staging from a seed no cohort
  uses. Every upload is made unique, so each is a real job.
- **Load:** it submits N of them through the TLS ingress, C at a time.
- **Samples:** `<engine> stats` every 5 s for each container's CPU and memory.
- **Report:** the host's identity (CPU model, cores, memory) is recorded beside the
  results.

**Targets** (`evaluation/targets.json`). Each is one metric on one cohort, with a
comparison, a threshold, and a minimum sample where it matters:
- **Provisional, `when_measured`** (the owner's choice). They are *not measured* until
  their real cohort has data, and then they must pass:
  - auto-accept precision ≥ 0.99 on `real-documents`, at least 300 accepted values, with
    coverage and the manual-review rate always reported beside it;
  - field exact match ≥ 0.99 on `real-clean-printed-documents`, at least 300 values;
  - character accuracy ≥ 0.99 on `real-clean-printed-text`, in each language, at least
    5,000 reference characters each.
- **Always binding:**
  - at least 6.95 pages per minute (10,000 pages a day; OD-19, to be confirmed by the
    owner) and a failure rate of 0, both from a benchmark on the declared
    `reference_server` only;
  - every English and Devanagari handwriting page routed to review;
  - no document failing in the evaluation.

Synthetic counterparts are shown beside each target, never counted for it.

**Release gates** (`make release-gates`, `scripts/release_gates.py`). Production approval
needs every blocking gate to pass:
- **Staging evidence** for this image's content ID: the tests, Trivy, the SBOMs and the
  offline test (Phase 12's checks, reused);
- **the install rehearsal**;
- **separation**;
- **freshness:** the evaluation ran complete, on this commit, from a clean tree;
- **reporting completeness:** size, languages, document types and conditions, and
  accuracy with coverage;
- **every target**, by its binding.

The script writes `reports/release-gates.json` and `.md`, and exits 1 when not approved.
Until a reference server is declared and benchmarked, the performance gates are *not
measured*, so approval is withheld, as the spec requires.

## Open decisions (defaults in force until decided)

| ID | Question | Default assumption |
|---|---|---|
| OD-1 | Production container base image | **Decided (ADR-0031):** `ubuntu:noble-20260917` by digest, with uv-provisioned Python 3.11 |
| OD-2 | Queue broker | **Decided (ADR-0029):** Valkey 8 (BSD-3-Clause) with Celery |
| OD-3 | Handwriting model | None selected. Candidates are license-reviewed in the handwriting phase; **stop and report** if none is clearly licensed |
| OD-4 | PaddleOCR coverage for te/hi/ta/kn/mr on CPU | **Measured on the synthetic corpus (Phase 4)**: see ACCURACY_EVALUATION.md. Real-scan accuracy remains unmeasured |
| OD-5 | Container/image vulnerability scanner | **Decided (ADR-0031):** Trivy 0.75.0 (`make image-scan`), database fetched at staging |
| OD-6 | API authentication | **Done for the API (ADR-0029):** every endpoint except health and the static review page needs a bearer token; tokens are in PostgreSQL in service mode, with a `submitter` role for integrations. Still open: a session login for the full review application (Phase 10, with OD-35); OIDC optional later |
| OD-7 | Test-client transport | Starlette warns that `httpx` is deprecated in favour of `httpx2`. Migration is held for approval (new dependency) |
| OD-8 | GitHub Actions pinning | Pinned to major tags today; pin to commit SHAs before the first protected-branch merge |
| OD-9 | Untracked empty `requirements.txt` | Left untouched; superseded by `requirements.in`/`.lock`. Propose deleting it |
| OD-10 | Git remote is a placeholder (`YOUR_USERNAME`) | No pushes or commits until the owner authorizes them and sets the remote |
| OD-11 | Dev host is Ubuntu 26.04, target is 24.04 | CI runs on `ubuntu-24.04`; containers will use a 24.04 base (OD-1) |
| OD-12 | Parser isolation | **Partly done:** parsing runs in worker processes (Phase 9) inside hardened containers (non-root, no capabilities, read-only root, memory and process limits, no network; ADR-0031). Still open: a separate sandbox process per document (e.g. seccomp-restricted subprocess) |
| OD-13 | Retention of rejected uploads | Deleted immediately; only the audit event (sha256, size, code) remains. Alternative: a quarantine hold for security review |
| OD-14 | Rotated PDF pages | `rotation` is recorded. Text bounding boxes are in unrotated page space with a top-left origin, while rendered images follow PDFium's display orientation. Layout (Phase 6) works in OCR-image coordinates, so it is unaffected; reconcile together with OD-25 |
| OD-15 | Upload spooling | **Done (ADR-0031):** in the image `TMPDIR` is `/data/tmp` on the document volume, created owner-only (0700); nginx streams bodies (`proxy_request_buffering off`) |
| OD-16 | Storage growth and retention | About 4–6 MB per scanned page at 300 DPI (about 40–60 GB/day at 10k pages/day). Default: none yet. Propose a retention period per tree (derived artefacts can be regenerated from originals) before Phase 11 |
| OD-17 | Per-language engine choice | **Decided 2026-10-22 on measurement:** Kannada → Tesseract-primary (PaddleOCR CER 0.388 vs Tesseract 0.013). Others PaddleOCR-primary (see OD-21) |
| OD-18 | Page-level 180° orientation for Indic scripts | **Resolved 2026-10-23 (ADR-0023):** Tesseract OSD orientation is applied before OCR for every script; measured correct on all upright/180° corpus pages. Evaluate PaddleOCR's document-orientation classifier (`PP-LCNet_x1_0_doc_ori`) or Tesseract OSD |
| OD-19 | OCR throughput vs 10k pages/day | About 6–7 s/page for PaddleOCR on a 2-core laptop (ACCURACY_EVALUATION.md). 10k pages/day is about 19 core-hours/day, so it must be measured on the reference server before sizing (Phase 11) |
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
| OD-33 | Calibration on real documents | Thresholds were fitted on synthetic documents only, and automatic acceptance stays off. Default: label consented real documents (or use approved reviews, `--from-reviews`) and refit before enabling it; at least 50 gated values per field |
| OD-34 | Off-host copy of the review history | **Partly done (ADR-0029):** in service mode the history and audit tables are append-only (trigger), so only a database superuser can rewrite them. Default: also copy them to write-once storage or a separate log host (Phase 11) |
| OD-35 | Review page sign-in | **Done (ADR-0030):** password sign-in with HttpOnly, SameSite=Strict session cookies and a CSRF token; the token-paste page is retired |
| OD-36 | Brute-force protection | Web sign-in locks an account after repeated wrong passwords (ADR-0030); tokens are 256-bit and failures are audited. Default: also add per-address rate limiting at the reverse proxy (Phase 11), which also limits name enumeration attempts across many accounts |
| OD-37 | Text cells in tables vs the second engine | A description cell the second engine reads differently blocks table verification, while numbers are corroborated by row checks. Default: keep (conservative); revisit with real documents |
| OD-38 | Corrections after re-extraction | A new extraction makes a new revision and earlier corrections are not carried over (values and candidates may have changed); the old revision is kept read-only. Alternative: re-apply corrections whose location and extracted value are unchanged |
| OD-39 | Name fields accepting identifiers | On 3 heavily degraded PAN cards the PAN number was extracted as the holder's name (confidence 0.999; caught by the page-quality gate). Candidate: name/text normalisers reject values that match an identifier format (PAN, GSTIN, Aadhaar) |
| OD-40 | PostgreSQL image version | `postgres:17.10-alpine` pinned by digest. 17.11 was released fewer than 14 days ago, so ADR-0007 holds it back. Default: move to the newest 17.x once it is 14 days old, after checking the 17.11 release notes for security fixes that affect us |
| OD-41 | Database and broker TLS | Both are reached only on the internal container network (or loopback in development), without TLS. Default: keep for a single host; require TLS (`sslmode=verify-full`, `rediss://`) if they ever run on another host |
| OD-42 | Backups and retention | **Done (ADR-0031):** `make backup` / `make restore` with SHA-256 sums and `ops verify`; `ops retention`. The operator schedules backups, keeps them on encrypted off-host media, and drills restores (OPERATIONS_RUNBOOK.md) |
| OD-43 | Valkey persistence | Append-only file on, so queued messages survive a restart (the reaper re-sends any that are lost). Default: keep; `appendfsync everysec` (the default) can lose about one second of messages, which the reaper recovers |
| OD-44 | libpq on hosts | Service mode needs the OS libpq (`sudo apt install libpq5` on Ubuntu; in the image in Phase 11). Without it, psycopg cannot load and the service-mode tests are skipped |
| OD-45 | Multi-factor sign-in and single sign-on | Passwords only. Options: TOTP as a second factor (stdlib HMAC, no dependency), or OIDC with the organisation's identity provider |
| OD-46 | Web application in local mode | Sign-in, admin and search need the database, so the web application needs service mode (`make services-up`); local mode keeps the API for scripts and tests |
| OD-47 | Browser testing | The application is unit-tested (Vitest with jsdom) and its API flows end to end against the live stack, but no automated real-browser test runs (no browser automation dependency was added). Default: manual check per release; consider Playwright (Apache-2.0) in CI |
| OD-48 | Personal data in the review history | The history keeps extracted, previous and corrected values (masked for Aadhaar) and is append-only, so retention cannot delete it with the document. Options: keep (audit value) with a documented retention basis; store only value hashes in future events; or export and purge per records policy by a superuser |
| OD-49 | Backup encryption | Backups are plain `pg_dump` and tar files in a 0700 directory. Default: store on encrypted media; option: encrypt with `age` or GPG at backup time (new tool, needs approval) |
| OD-50 | Host egress rules | The nftables template is syntax-checked but not applied or tested here (needs root on the production host). Container egress is blocked and tested regardless (layer 1) |
| OD-51 | Release signing key custody | The key is created at staging with a passphrase (`make signing-key`) outside the repository. Its public half and fingerprint reach the site by a separate channel. Open: a hardware token (`ssh-keygen -t ed25519-sk`), a second signer, and a rotation and revocation procedure |
| OD-52 | Byte-reproducible images | Inputs are pinned, but the image and the OpenCV wheel carry build timestamps, so two builds differ. Images are identified by content ID instead. Open: `SOURCE_DATE_EPOCH` and rewritten timestamps |
| OD-53 | Reference CPU server | Not yet identified. The performance gates accept only a benchmark from the host declared in `evaluation/targets.json` (`reference_server`). Until then, throughput and latency are measured on the staging laptop and reported as such |
| OD-54 | Throughput target | 6.95 pages per minute (10,000 pages a day over 24 hours, from OD-19). To be confirmed with the server's sizing and the working hours; a latency (p95) target is not set |
| OD-55 | Real evaluation documents | None yet. The 99% targets stay *not measured* until `evaluation/real/` has consented documents with ground truth, in the sizes the targets require (evaluation/real/README.md) |
| OD-56 | Very sparse pages treated as blank | Phase 13 found that a page with five lines of thin handwriting (0.2% ink) was skipped as blank, unread and not routed to review. The intensity-spread test no longer decides alone (fixed). Pages under 0.1% ink, such as a single short line or a signature, are still skipped as blank. Options: route skipped blank pages to review, or a connected-component test that tells strokes from specks. Default: unchanged until decided |
