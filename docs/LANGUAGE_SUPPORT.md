# Language Support Matrix

The single source of truth in code is `newgen_ocr/ocr/languages.py` (served at
`GET /api/v1/languages`). This document adds provenance and measured results. A language is
enabled only with documented model provenance (MODEL_INVENTORY.md) and measured results
(ACCURACY_EVALUATION.md). **All results below are on synthetic pages; none are on real
documents yet.**

## Printed text

| Language | Script | Engines (preferred first) | PaddleOCR model | Tesseract model | PaddleOCR CER | Tesseract CER | Status |
|---|---|---|---|---|---|---|---|
| English (en) | Latin | paddle, tesseract | `en_PP-OCRv5_mobile_rec` | `tessdata_best/eng` | 0.000 | 0.000 | Enabled |
| Hindi (hi) | Devanagari | paddle, tesseract | `devanagari_PP-OCRv5_mobile_rec` | `tessdata_best/hin` | 0.007 | 0.000 | Enabled |
| Marathi (mr) | Devanagari | paddle, tesseract | `devanagari_PP-OCRv5_mobile_rec` | `tessdata_best/mar` | 0.014 | 0.002 | Enabled |
| Telugu (te) | Telugu | paddle, tesseract | `te_PP-OCRv5_mobile_rec` | `tessdata_best/tel` | 0.009 | 0.003 | Enabled |
| Tamil (ta) | Tamil | paddle, tesseract | `ta_PP-OCRv5_mobile_rec` | `tessdata_best/tam` | 0.008 | 0.000 | Enabled |
| Kannada (kn) | Kannada | **tesseract only** | (`ka_PP-OCRv3_mobile_rec`: CER 0.388, **disabled**) | `tessdata_best/kan` | — | 0.013 | Enabled (Tesseract) |

Mixed-script pages (an Indian script plus English, either dominant) are supported for every
row: measured CER ≤ 0.009 on all 10 mixed test pages (ACCURACY_EVALUATION.md). Two different
Indian scripts on one page are flagged `mixed_script` for review, because a PaddleOCR
recogniser covers one Indian script.

## Tables and layout (Phase 6)

Layout and table structure are script-independent (they work on the page image) and were
measured on ruled tables in every language (ACCURACY_EVALUATION.md, synthetic): structure exact
and verified against ruling lines for all six. Cell text comes from the language's OCR engine.
**Kannada (Tesseract) skips text inside ruled grids on the page pass**: such cells are re-read
from their crops, and any cell still unread is flagged `table_cell_unread` for review.

## Structured extraction (Phase 7)

Extraction rules are script-independent. Digits in every Indian script are normalised, and
field labels can be in any language. Built-in label dictionaries:
- **English:** every field.
- **Hindi:** date, total, name, father's name, date of birth, address, gender, invoice
  number, Aadhaar number and PAN labels.
- **Marathi:** total.
- **Telugu, Tamil, Kannada:** date and total on receipts.

Measured only on English and Hindi documents (ACCURACY_EVALUATION.md). Add labels for other
languages in a custom schema and measure them before relying on them.

## Handwriting

| Language | Status | Engine | Evidence |
|---|---|---|---|
| English | **Experimental**: every result routed to review | `paddle_handwriting` (`PP-OCRv5_server_rec`) | Synthetic handwriting fonts only: CER 0.023 (worst 0.176, word spaces dropped), *worse* than the printed recogniser (0.005) on the same pages. No real handwriting measured |
| Hindi, Marathi, Telugu, Tamil, Kannada | **Unsupported**: pages returned `unsupported` + review | none | No openly licensed model with clean training-data provenance exists (ADR-0024) |

Handwriting is never `supported` until measured on real samples
(`evaluation/handwriting/README.md`) and added to `NGOCR_OCR_HANDWRITING_VERIFIED_LANGUAGES`.

## Unsupported and uncertain input: review routing

Nothing is guessed. A page that cannot be recognised reliably is returned with status
`unsupported` (not OCRed) or with `review.needs_review = true`, plus machine-readable reasons:

| Reason | Trigger |
|---|---|
| `unsupported_script` | Script identified but not in this matrix (e.g. Bengali, Arabic, Cyrillic), or unsupported characters in the output |
| `script_uncertain` | `languages: ["auto"]` and the script could not be identified confidently (e.g. Gujarati, noise, sideways pages whose orientation OSD could not determine) |
| `script_mismatch` | Explicit language whose recogniser returned text mostly in a different script |
| `language_not_enabled` | The requested engine is not enabled for the language (e.g. PaddleOCR + Kannada) |
| `handwriting_unsupported` / `handwriting_unverified` | Handwriting in a language without an engine / with an unverified engine |
| `mixed_script` | Two different Indian scripts on one page |
| `low_confidence` / `low_confidence_lines` | Page mean < 0.85, or any line < 0.70 |
| `empty_result`, `ocr_failed` | No text on a non-blank page; engine failure |
| `low_quality` | Page flagged `blurry` or `low_resolution` by quality assessment |
| `engine_disagreement` | With `compare`, the engines agree below 0.85 |

**Confidence is not a reliable error detector by itself.** PaddleOCR's Kannada output is 39%
wrong at mean confidence 0.94. That is why languages are enabled per engine only by
measurement, and why the other signals exist.
