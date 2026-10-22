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

Stages up to and including image quality assessment and preprocessing are **implemented**
(Phases 2–3, synchronous, see ADR-0012). Document/language classification onwards is planned.

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

### OCR engine adapter [planned]

```python
class OcrEngine(Protocol):
    name: str
    version: str
    def supports(self, language: str, script_kind: Literal["printed", "handwritten"]) -> bool: ...
    def recognize(self, page: PageImage, languages: Sequence[str]) -> OcrResult: ...
```

Candidates: PaddleOCR (primary), Tesseract (independent baseline/fallback), a locally hosted
handwriting model (to be selected after license review). The pipeline depends only on the
protocol, so engines can be added or swapped without touching other stages.

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
  db/                  SQLAlchemy models, Alembic migrations             [planned P4]
  worker/              Celery app + idempotent tasks                     [planned P4]
  ocr/                 engine protocol + Paddle/Tesseract adapters       [planned P5]
  evaluation/          CER/WER + field metrics harness                   [planned P5]
  classification/      document type + language/script detection         [planned P6]
  layout/              reading order, tables, key-value pairs            [planned P6]
  extraction/          field extraction rules/templates                  [planned P7]
  validation/          field validators, confidence aggregation          [planned P7]
  export/              JSON / CSV / Excel writers                        [planned P7]
  review/              review queue logic                                [planned P8]
frontend/              React + TypeScript review UI                      [planned P8]
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
| Auth | Not yet implemented. See DECISIONS.md open decision OD-6 [planned P4] |
| Document storage | Local filesystem, originals separate from derived, atomic commit (ADR-0013) [implemented] |
| Audit | JSONL lifecycle events without content or filenames (ADR-0014) [implemented] |
| Untrusted parsing | PDFium (serialised by a lock) and Pillow restricted to the detected format; in-process for now (OD-12) [implemented] |
| OpenCV supply | PyPI wheel in development; minimal source build required in production, enforced at startup (ADR-0015) [guard implemented, build planned P4] |
| Source preservation | Source page images are immutable; preprocessing outputs live per profile beside them (ADR-0018) [implemented] |
| Egress control | Host firewall / isolated container network. See OFFLINE_DEPLOYMENT.md [planned P10]. FastAPI telemetry disabled in code (ADR-0008) [implemented] |
