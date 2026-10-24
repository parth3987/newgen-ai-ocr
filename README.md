# New Gen AI OCR: Precision Data Extraction

A locally executed, open-source AI OCR platform for multilingual document processing,
scanned PDFs and handwriting recognition.

## Objectives
- CPU-based OCR processing
- Multilingual document extraction (English, Telugu, Hindi, Tamil, Kannada, Marathi)
- Scanned and native PDF processing, tables, forms, batch jobs
- Local-only inference that works with outbound network access blocked
- No external LLM or hosted AI APIs

## Status

Phase 11 (security and open-source compliance) is complete. The production deployment is a
hardened container stack behind a TLS ingress, with:
- a minimal source-built OpenCV and hash-verified models;
- no outbound network route (proved by `make offline-test`);
- tested backup and restore;
- retention rules;
- Trivy scans and SBOMs.

See [THREAT_MODEL](docs/THREAT_MODEL.md) and [SECURITY_REVIEW](docs/SECURITY_REVIEW.md).
Phase 10 (React frontend) is complete. The web application at `/app` covers sign-in, batch
upload with live progress, documents and results with search, page previews and OCR text,
extracted fields and tables, source-linked review and correction, exports, and admin
monitoring and audit views. It calls only this service's API and loads nothing external
(see [ADR-0030](docs/DECISIONS.md)).
Phase 9 (database, background jobs and APIs) is complete. In service mode, PostgreSQL holds
documents, jobs, results, review and audit records. Celery workers on Valkey run the
pipeline, and uploads return `202` with a job. Workers that crash are recovered from leases,
and duplicate submissions return the original job. Every endpoint needs a bearer token (see
[ADR-0029](docs/DECISIONS.md) and the Jobs section of
[API_SPECIFICATION](docs/API_SPECIFICATION.md)).
Phase 8 (confidence, verification and human review) is complete: every extracted value
passes field-specific gates before it counts as verified (a confidence threshold calibrated
on held-out ground truth, agreement of a second OCR engine, business-rule checks, clean
source pages). Everything else goes to a review queue with source crops, reason-coded
corrections, four-eyes approval and a tamper-evident history (see
[REVIEW](docs/REVIEW.md)). Automatic acceptance is off by default, and thresholds have so
far been fitted on synthetic documents only.
Phase 7 (structured data extraction without an LLM) is complete: rule and template
classification of GST invoices, receipts, bank statements, PAN and Aadhaar cards;
configurable field schemas; normalised, validated fields and table records with source
evidence on every value; arithmetic and cross-field checks; JSON/CSV/Excel export (see
[EXTRACTION](docs/EXTRACTION.md)). Missing values stay missing: on the synthetic test set no
value was invented.
Phase 6 (layout, tables and document structure) is complete: layout regions, reading order
for multi-column pages, headers/footers, table structure with merged cells (verified against
ruling lines on ruled tables), evidence-only key-value pairs and an inspection overlay, all
offline. Unsupported structures are recorded and routed to review, never invented (see
[ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md); synthetic test set so far).
Phase 5 (language and handwriting support) is complete: per-page script identification
(`languages: ["auto"]`), mixed Indian-script/English pages, a review path for unsupported or
uncertain pages, and experimental English handwriting (see
[LANGUAGE_SUPPORT](docs/LANGUAGE_SUPPORT.md)). Phase 4 (OCR engines) is complete. The service ingests PDF/JPEG/PNG/TIFF safely, uses
embedded text from native PDF pages, assesses and preprocesses scanned pages, and runs
PaddleOCR (primary; Tesseract for Kannada) or Tesseract (baseline/fallback) fully offline with pinned,
hash-verified models for English, Hindi, Marathi, Telugu, Tamil and Kannada. Accuracy and
throughput have been measured only on a synthetic corpus so far (see
[ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md)). See
[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), GNU make, `tesseract-ocr` and `libpq5`
(apt); Node 20+ for `make frontend`; Podman or Docker for `make services-up` and `make secrets`.

```bash
make venv              # Python 3.11 venv from hash-pinned locks
make models            # staging only: fetch + verify pinned OCR/layout models (~450 MB)
make check             # lint, types, tests, security, licenses
make run               # API on http://127.0.0.1:8000 (local mode: synchronous)
curl localhost:8000/health/ready
TOKEN=$(.venv/bin/python -m newgen_ocr.auth create --user me --role admin)
AUTH="Authorization: Bearer $TOKEN"
curl -H "$AUTH" -F file=@document.pdf -F profile=standard localhost:8000/api/v1/documents
curl -H "$AUTH" -X POST -H 'Content-Type: application/json' -d '{"languages":["te"]}' \
     localhost:8000/api/v1/documents/<document_id>/ocr
curl -H "$AUTH" -X POST localhost:8000/api/v1/documents/<document_id>/layout      # tables
curl -H "$AUTH" -X POST localhost:8000/api/v1/documents/<document_id>/extraction  # fields
curl -H "$AUTH" -o out.xlsx \
     'localhost:8000/api/v1/documents/<document_id>/extraction/auto/export?format=xlsx'
```

Service mode (PostgreSQL, Valkey and workers; uploads queue the whole pipeline) and the web
application (`make frontend`, then `http://127.0.0.1:8000/app/`): see "Service mode" and
"Web application" in [OPERATIONS_RUNBOOK](docs/OPERATIONS_RUNBOOK.md). Production containers
(`make images`, `make stack-up`, then `https://127.0.0.1:8443/app/`): see "Production stack"
there, and [OFFLINE_DEPLOYMENT](docs/OFFLINE_DEPLOYMENT.md).

Configuration: see [.env.example](.env.example).

## Documentation

| Doc | Purpose |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, pipeline, module boundaries |
| [IMPLEMENTATION_PLAN](docs/IMPLEMENTATION_PLAN.md) | Phases, gates, status |
| [DECISIONS](docs/DECISIONS.md) | ADRs and open decisions |
| [API_SPECIFICATION](docs/API_SPECIFICATION.md) | Endpoints and error codes |
| [SECURITY](docs/SECURITY.md) | Controls and their status |
| [THREAT_MODEL](docs/THREAT_MODEL.md) | Data flows, trust boundaries, STRIDE per element |
| [SECURITY_REVIEW](docs/SECURITY_REVIEW.md) | Scan results and manual reviews (Phase 11) |
| [OFFLINE_DEPLOYMENT](docs/OFFLINE_DEPLOYMENT.md) | Staging, bundle and disconnected install |
| [TESTING](docs/TESTING.md) | Test strategy and commands |
| [ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md) | Metrics and dataset rules |
| [MODEL_INVENTORY](docs/MODEL_INVENTORY.md) | Models, hashes and licenses |
| [THIRD_PARTY_NOTICES](docs/THIRD_PARTY_NOTICES.md) | Dependency licenses |
| [OPERATIONS_RUNBOOK](docs/OPERATIONS_RUNBOOK.md) | Running and troubleshooting |
| [PERFORMANCE](docs/PERFORMANCE.md) | Measured CPU timings and sizing notes |
| [EXTRACTION](docs/EXTRACTION.md) | Structured extraction: classification, fields, evidence, checks, export |
| [EXTRACTION_SCHEMAS](docs/EXTRACTION_SCHEMAS.md) | Built-in document types (generated) |
| [LANGUAGE_SUPPORT](docs/LANGUAGE_SUPPORT.md) | Supported languages, handwriting status, review routing |
| [CHANGELOG](CHANGELOG.md) | Changes by phase |
