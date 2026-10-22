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

## 2. Processing pipeline [planned]

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

### Evidence model [planned]

Every extracted value carries: page number, bounding box or text span, engine name and
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
  errors.py            ErrorCode enum, AppError, error envelope          [implemented]
  api/
    app.py             create_app() factory                              [implemented]
    middleware.py      request id + access log                           [implemented]
    routes/health.py   /health/live, /health/ready                       [implemented]
    routes/...         documents, jobs, results, review                  [planned]
  db/                  SQLAlchemy models, Alembic migrations             [planned P2]
  storage/             quarantine + content-addressed document store     [planned P2]
  ingestion/           upload validation (type sniffing, size, pages)    [planned P2]
  worker/              Celery app + idempotent tasks                     [planned P2]
  pdf/                 text-layer extraction, page rendering             [planned P3]
  imaging/             quality assessment, preprocessing (OpenCV)        [planned P3]
  ocr/                 engine protocol + Paddle/Tesseract adapters       [planned P4]
  evaluation/          CER/WER + field metrics harness                   [planned P4]
  classification/      document type + language/script detection         [planned P5]
  layout/              reading order, tables, key-value pairs            [planned P5]
  extraction/          field extraction rules/templates                  [planned P6]
  validation/          field validators, confidence aggregation          [planned P6]
  export/              JSON / CSV / Excel writers                        [planned P6]
  review/              review queue logic                                [planned P7]
frontend/              React + TypeScript review UI                      [planned P7]
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
| Auth | Not yet implemented. See DECISIONS.md open decision OD-6 [planned P2] |
| Egress control | Host firewall / isolated container network. See OFFLINE_DEPLOYMENT.md [planned] |
