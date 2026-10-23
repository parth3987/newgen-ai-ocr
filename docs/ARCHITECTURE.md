# Architecture

Status legend: **[implemented]** exists in the code today; **[planned]** is the target
design and does not exist yet. Keep this file in step with the code.

## 1. Context

A self-hosted, CPU-only OCR and document-intelligence platform. Production processing runs
with outbound network access blocked. No external LLM, hosted OCR or inference API is used.

```
             ┌─────────────── trusted host / private network (no egress) ───────────────┐
 Users ──►   │  React UI ─► FastAPI API ─► PostgreSQL                                    │
 Systems ──► │                  │   ▲                                                    │
             │                  ▼   │                                                    │
             │               Valkey (broker) ─► Celery workers ─► local OCR models       │
             │                                        │                                 │
             │                                        ▼                                 │
             │                              local document storage (data/)              │
             └──────────────────────────────────────────────────────────────────────────┘
```

## 2. Processing pipeline

Stages up to and including structured extraction and export are **implemented** (Phases 2–7,
synchronous, see ADR-0012): script/language identification and experimental English
handwriting (Phase 5), and layout, reading order, tables and key-value pairs derived from a
stored OCR run (Phase 6, ADR-0025), and rule/template classification, field and table
extraction, validation, checks and JSON/CSV/Excel export from a stored layout run (Phase 7,
ADR-0026), and verification gates with calibrated thresholds, a review queue, corrections,
approval and a minimal review page (Phase 8, ADR-0027/0028). Persistence and jobs are
planned. All Paddle inference runs on one thread per process (ADR-0020 addendum).

```
upload → file validation + quarantine → PDF text extraction or page rendering
→ image quality assessment → preprocessing → document + language classification
→ OCR engine selection → printed / handwriting recognition → layout + table reconstruction
→ structured field extraction → validation + confidence → human review (when required)
→ PostgreSQL + local storage → JSON / CSV / Excel / API delivery
```

Each stage is a pure-ish function over a typed page/document model and is invoked by an
idempotent Celery task keyed on `(document_id, stage, input_hash)`, so retries never
duplicate results.

### Evidence model [partially implemented]

Embedded PDF text already carries page number and segment bounding boxes (points, top-left
origin) plus the PDFium version in the manifest. Every extracted value will carry: page number, bounding box or text span, engine name and
version, model id and hash, confidence indicator, validation status and review status.

### OCR engine adapter [implemented, Phase 4]

```python
class OcrEngine(Protocol):
    name: str
    version: str
    def supports(self, language: str, script_kind: Literal["printed", "handwritten"]) -> bool: ...
    def recognize(self, page: PageImage, languages: Sequence[str]) -> OcrResult: ...
```

Implemented: PaddleOCR 3.7 / PaddlePaddle 3.2.2 (primary) and Tesseract 5 CLI (baseline and
fallback), both reading only hash-verified local models (ADR-0020..0022). The actual protocol
is `unavailable_reason()`, `languages()`, `info(languages)` and `recognize(image, languages)`.
A handwriting engine is planned. The pipeline depends only on the protocol.

## 3. Module boundaries

```
src/newgen_ocr/
  __init__.py          version                                           [implemented]
  config.py            Settings (env NGOCR_*, frozen)                    [implemented]
  logging_config.py    JSON/console structured logging                   [implemented]
  errors.py            ErrorCode enum, AppError (framework-free)         [implemented]
  audit.py             append-only JSONL audit events                    [implemented P2]
  api/
    app.py             create_app() factory                              [implemented]
    errors.py          code→HTTP status, error envelope, handlers        [implemented]
    middleware.py      request id, access log, no-store/nosniff          [implemented]
    body_limit.py      streaming request-body size limit (ASGI)          [implemented P2]
    routes/health.py   /health/live, /health/ready                       [implemented]
    routes/documents.py upload, manifest, page text, page image          [implemented P2]
    routes/preprocessing.py profiles, runs, preprocessed images          [implemented P3]
    routes/ocr.py      engines, languages, OCR runs, comparison          [implemented P4-5]
    routes/...         jobs, results, review                             [planned]
  ingestion/
    formats.py         signature detection, extension check              [implemented P2]
    models.py          DocumentManifest / PageManifest / PageText        [implemented P2]
    service.py         receive→quarantine→validate→process→commit        [implemented P2]
  pdf/
    analysis.py        per-page OCR-need policy (pure functions)         [implemented P2]
    processor.py       PDFium open/validate, text+bbox, render           [implemented P2]
  imaging/
    loader.py          restricted Pillow decode, normalisation           [implemented P2]
    geometry.py        text-axis + skew estimation, rotations            [implemented P3]
    quality.py         quality metrics and flags                         [implemented P3]
    filters.py         denoise, contrast, illumination, threshold        [implemented P3]
    runtime.py         OpenCV threads, runtime info, production guard    [implemented P3]
  preprocessing/
    profiles.py        named profiles (built-in + custom)                [implemented P3]
    pipeline.py        per-page step execution and recording             [implemented P3]
    service.py         per-document runs, storage, audit                 [implemented P3]
    models.py          PreprocessingRun / PagePreprocessing / StepRecord [implemented P3]
  storage/local.py     originals / derived / staging / quarantine        [implemented P2]
  db/                  SQLAlchemy models, Alembic migrations             [planned P8]
  worker/              Celery app + idempotent tasks                     [planned P8]
  ocr/
    engine.py          OcrEngine protocol, reading order, page orientation [implemented P4]
    models.py          OcrLine / OcrPage / OcrRun / OcrComparison schema   [implemented P4]
    model_store.py     models.lock.json resolution + SHA-256 verification  [implemented P4]
    paddle_runtime.py  PaddleX offline lockdown (only PaddleX importer)    [implemented P4]
    paddle_engine.py   PaddleOCR adapter (primary)                         [implemented P4]
    tesseract_engine.py Tesseract CLI adapter (baseline/fallback)          [implemented P4]
    compare.py         engine agreement + review flag (never merges)       [implemented P4]
    service.py         per-page plans: script ID, routing, fallback, review [implemented P4-5]
    languages.py       supported-language matrix (single source of truth) [implemented P5]
    script_detect.py   OSD orientation + census script identification      [implemented P5]
    language_id.py     text script census, Hindi/Marathi identification    [implemented P5]
  evaluation/
    metrics.py         CER / WER / agreement (vectorised Levenshtein)      [implemented P4]
    corpus.py          synthetic multilingual corpus with ground truth     [implemented P4]
    layout_corpus.py   synthetic table/layout pages with exact ground truth [implemented P6]
    layout_metrics.py  table association F1, reading order, KV scores     [implemented P6]
    (field metrics)                                                       [planned]
  layout/
    models.py          LayoutRun / PageLayout / Table / KeyValuePair schema [implemented P6]
    detector.py        PP-DocLayout + table classifier + SLANet_plus wrapper [implemented P6]
    table_structure.py token parsing, ruling trace, evidence checks        [implemented P6]
    reading_order.py   XY-cut reading order, column count                 [implemented P6]
    key_value.py       evidence-only key-value pairs                       [implemented P6]
    overlay.py         inspection overlay PNG                              [implemented P6]
    service.py         region/line assignment, tables, review, storage     [implemented P6]
  extraction/          rules/templates only, no LLM (ADR-0026)           [implemented P7]
    schema.py          document-type schema format + registry (JSON)
    schemas/*.json     built-in types: gst_invoice, receipt, bank_statement, pan_card, aadhaar_card
    document.py        lines/tables view of layout + OCR runs (or PDF text)
    classify.py        template and weighted-rule classification
    extract.py         candidates (template/label/section/table row/format) + resolution
    normalize.py       dates, amounts, identifiers, addresses, Indian-script digits
    identifiers.py     GSTIN mod-36, PAN, Aadhaar Verhoeff + masking, IFSC
    checks.py          arithmetic and cross-field consistency checks
    validation.py      generated Pydantic record / row models
    export.py          JSON / CSV / XLSX (formula-injection safe)
    service.py         pipeline, review routing, storage, audit
  auth/                local hashed bearer tokens + roles (ADR-0027)     [implemented P8]
  review/              verification and review (ADR-0028)                [implemented P8]
    acceptance.py      field gates: calibrated threshold, engines, checks, sources
    calibration.py     threshold fitting (Wilson bound, type pooling), held-out evaluation
    engines.py         field-level cross-check against the second OCR engine
    corrections.py     normalised corrections, reviewed result (re-validated, re-checked)
    store.py           items + hash-chained history on the local store (flock)
    crops.py           evidence crops and page previews (Aadhaar redaction)
    service.py         queue, claim/correct/approve/reject, patterns
    ui/                minimal review page (static HTML/CSS/JS, strict CSP)
frontend/              React + TypeScript review application             [planned P10]
```

Dependency rule: `api` and `worker` may depend on domain modules; domain modules never import
`api`, `worker` or FastAPI/Celery.

## 4. Cross-cutting concerns

| Concern | Current state |
|---|---|
| Configuration | `pydantic-settings`, `NGOCR_` prefix, immutable; explicit `Settings` injected into `create_app()` (no import-time global app) [implemented] |
| Logging | JSON lines to stdout; metadata only, never document content; request id via contextvar; HTTP-client loggers capped at WARNING [implemented] |
| Errors | Stable `ErrorCode` values in a uniform envelope; unhandled errors return a generic 500 and are logged with traceback [implemented] |
| Health | Liveness (process up) vs readiness (dependencies usable) [implemented; readiness covers `data_dir` only] |
| API docs | Swagger UI/ReDoc disabled (they load CDN assets); OpenAPI JSON at `/api/v1/openapi.json` [implemented] |
| Auth | Not yet implemented. See DECISIONS.md open decision OD-6 [planned P8] |
| Document storage | Local filesystem, originals separate from derived, atomic commit (ADR-0013) [implemented] |
| Audit | JSONL lifecycle events without content or filenames (ADR-0014) [implemented] |
| Untrusted parsing | PDFium (serialised by a lock) and Pillow restricted to the detected format; in-process for now (OD-12) [implemented] |
| Model integrity | `models.lock.json` pins revisions and SHA-256 per file; verified before first use; never downloaded at runtime (ADR-0021) [implemented] |
| OpenCV supply | PyPI wheel in development; minimal source build required in production, enforced at startup (ADR-0015) [guard implemented, build planned P8] |
| Source preservation | Source page images are immutable; preprocessing outputs live per profile beside them (ADR-0018) [implemented] |
| Egress control | Host firewall / isolated container network. See OFFLINE_DEPLOYMENT.md [planned P10]. FastAPI telemetry disabled in code (ADR-0008) [implemented] |
