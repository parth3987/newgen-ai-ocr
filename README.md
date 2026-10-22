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

Phase 5 (language and handwriting support) is complete: per-page script identification
(`languages: ["auto"]`), mixed Indian-script/English pages, a review path for unsupported or
uncertain pages, and experimental English handwriting (see
[LANGUAGE_SUPPORT](docs/LANGUAGE_SUPPORT.md)). Phase 4 (OCR engines) is complete. The service ingests PDF/JPEG/PNG/TIFF safely, uses
embedded text from native PDF pages, assesses and preprocesses scanned pages, and runs
PaddleOCR (primary; Tesseract for Kannada) or Tesseract (baseline/fallback) fully offline with pinned,
hash-verified models for English, Hindi, Marathi, Telugu, Tamil and Kannada. Accuracy and
throughput have been measured only on a synthetic corpus so far (see
[ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md)). Field extraction, layout and review are
still to come. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), GNU make, `tesseract-ocr` (apt); Docker for
`make secrets`.

```bash
make venv              # Python 3.11 venv from hash-pinned locks
make models            # staging only: fetch + verify pinned OCR models (~198 MB)
make check             # lint, types, tests, security, licenses
make run               # API on http://127.0.0.1:8000
curl localhost:8000/health/ready
curl -F file=@document.pdf -F profile=standard localhost:8000/api/v1/documents
curl -X POST -H 'Content-Type: application/json' -d '{"languages":["te"]}' \
     localhost:8000/api/v1/documents/<document_id>/ocr
```

Configuration: see [.env.example](.env.example).

## Documentation

| Doc | Purpose |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, pipeline, module boundaries |
| [IMPLEMENTATION_PLAN](docs/IMPLEMENTATION_PLAN.md) | Phases, gates, status |
| [DECISIONS](docs/DECISIONS.md) | ADRs and open decisions |
| [API_SPECIFICATION](docs/API_SPECIFICATION.md) | Endpoints and error codes |
| [SECURITY](docs/SECURITY.md) | Threat model and controls |
| [OFFLINE_DEPLOYMENT](docs/OFFLINE_DEPLOYMENT.md) | Staging, bundle and disconnected install |
| [TESTING](docs/TESTING.md) | Test strategy and commands |
| [ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md) | Metrics and dataset rules |
| [MODEL_INVENTORY](docs/MODEL_INVENTORY.md) | Models, hashes and licenses |
| [THIRD_PARTY_NOTICES](docs/THIRD_PARTY_NOTICES.md) | Dependency licenses |
| [OPERATIONS_RUNBOOK](docs/OPERATIONS_RUNBOOK.md) | Running and troubleshooting |
| [PERFORMANCE](docs/PERFORMANCE.md) | Measured CPU timings and sizing notes |
| [LANGUAGE_SUPPORT](docs/LANGUAGE_SUPPORT.md) | Supported languages, handwriting status, review routing |
| [CHANGELOG](CHANGELOG.md) | Changes by phase |
