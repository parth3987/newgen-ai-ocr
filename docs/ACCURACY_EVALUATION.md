# Accuracy Evaluation

Status: **synthetic baseline measured (Phase 4, 2026-10-22). No real-document measurements
exist yet.** This project makes no accuracy claim (in particular no universal "99%") beyond
what is measured below, under the stated conditions.

## Metrics

| Level | Metric |
|---|---|
| Text | Character Error Rate (CER), Word Error Rate (WER), after documented Unicode normalization (NFC; script-specific rules for Indic scripts recorded per language) |
| Field | Exact-match accuracy, normalized-match accuracy, precision/recall of field detection, per critical field |
| Table | Cell-level accuracy and structure accuracy (row/column assignment) |
| Classification | Per-class precision/recall, confusion matrix |
| Confidence calibration | Accuracy within confidence bands; review-routing precision/recall |
| Throughput | Pages/minute per CPU core, p50/p95 latency per page, by document type |

## Reporting dimensions

Every result is reported **separately** by language (en, te, hi, ta, kn, mr), document type,
scan condition (clean / degraded / photographed; DPI band), script kind (printed /
handwritten) and engine/model version. Aggregates are never reported alone.

## Dataset rules

- Ground truth is double-keyed with adjudication; inter-annotator agreement is recorded.
- Splits are fixed and versioned (dev / test). The test split is never used for tuning.
- Every sample's provenance and license or consent is recorded. Production documents are
  used only on-premises with authorization and never leave the environment.
- Minimum sample sizes per reported cell are defined before measurement; cells below the
  minimum are reported as "insufficient data".
- Report confidence intervals (bootstrap) along with point estimates.

## Acceptance criteria

Thresholds are set per critical field and per language/document type with the business owner
before Phase 6 sign-off and recorded here. Fields that miss their threshold must route to
human review.

## Baseline corpus (Phase 4): synthetic, printed

`evaluation/corpus/text/<lang>.txt` holds 16 document-style lines per language (invoice and
notice phrasing; the English set includes digits, codes and punctuation). `make eval`
renders them (`newgen_ocr/evaluation/corpus.py`) with two Noto fonts per script (Sans,
Serif), using Pillow's raqm/HarfBuzz shaping, so conjuncts and vowel signs are correct. Three
conditions:

| Condition | Rendering |
|---|---|
| `clean` | 300 DPI, perfect render |
| `scan` | 200 DPI, 1.2° skew, Gaussian blur σ0.7, noise σ8, JPEG quality 70 |
| `lowres` | 150 DPI, perfect render |

36 pages (6 languages × 2 fonts × 3 conditions), exact ground truth. **Limits:** printed,
single-column, one font size, no tables, stamps, handwriting, photos or real scanner
artefacts. Results show model and pipeline correctness and relative differences between
languages; they are **not** estimates of accuracy on real documents. CER/WER are
whole-page, after normalisation (NFC, whitespace), with case and punctuation counted.

## Results: PaddleOCR (Phase 4)

Engine: PaddleOCR 3.7.0 / PaddlePaddle 3.2.2 (oneDNN), `PP-OCRv5_mobile_det` plus the
per-language recogniser in MODEL_INVENTORY.md. Text-line orientation is English only
(ADR-0021). No preprocessing. Raw data: `reports/ocr_eval_paddle_final.json` (not committed;
regenerate with `make eval`).

| Language | Pages | CER mean | CER worst page | WER mean | Verdict on this corpus |
|---|---|---|---|---|---|
| English (en) | 6 | 0.000 | 0.001 | 0.002 | Excellent |
| Hindi (hi) | 6 | 0.007 | 0.011 | 0.033 | Good |
| Marathi (mr) | 6 | 0.014 | 0.020 | 0.081 | Good; errors mostly in conjunct-heavy words |
| Telugu (te) | 6 | 0.009 | 0.015 | 0.058 | Good |
| Tamil (ta) | 6 | 0.008 | 0.011 | 0.080 | Good |
| **Kannada (kn)** | 6 | **0.388** | 0.394 | **1.000** | **Not usable.** The PP-OCRv3 Kannada model drops all spaces and many characters. Kannada is routed to Tesseract (below) |

Per condition, mean CER ranges stay within the bands above for every language except
Kannada: degradation from `clean` to `scan` or `lowres` is at most about 0.01 CER.

Findings that changed the design (ADR-0021):
- With the text-line orientation classifier on, Hindi measured CER 0.897 and Marathi 0.887
  (every upright line flipped), and one Tamil serif scan measured 0.707. With it off, those
  fell to 0.007, 0.007 and 0.009. The classifier is now English-only.

## Results: Tesseract (Phase 4)

Engine: Tesseract 5.5.0 (Ubuntu apt), LSTM (`--oem 1`), `--psm 3`, `tessdata_best` from the
pinned revision; word-final zero-width joiners removed (ADR-0022). No preprocessing. Raw data:
`reports/ocr_eval_tesseract.json`.

| Language | Pages | CER mean | CER worst page | WER mean | Note |
|---|---|---|---|---|---|
| English (en) | 6 | 0.000 | 0.001 | 0.004 | Excellent |
| Hindi (hi) | 6 | 0.000 | 0.000 | 0.000 | Excellent |
| Marathi (mr) | 6 | 0.002 | 0.002 | 0.015 | Excellent |
| Telugu (te) | 6 | 0.003 | 0.003 | 0.019 | Excellent |
| Tamil (ta) | 6 | 0.000 | 0.001 | 0.002 | Excellent, after joiner clean-up (it was CER 0.064 / WER 0.60 before; see below) |
| Kannada (kn) | 6 | 0.013 | 0.029 | 0.139 | Sans font perfect (CER 0.000–0.003). Serif font CER about 0.025: narrow word spacing makes Tesseract merge adjacent words |

Findings that changed the code:
- Tesseract's `tsv` output option is a config *file* in the OS tessdata directory, which
  we do not use. The first runs therefore returned plain text and parsed as empty pages.
  The adapter now sets `tessedit_create_tsv=1` directly, and treats non-TSV output as
  `OCR_FAILED`, never as an empty page.
- Tesseract appends U+200C (ZWNJ) after word-final virama in Tamil. It is invisible, but it
  inflated Tamil WER to 0.60 and would break exact field matching. Joiners at word boundaries
  are now removed; mid-word joiners, which carry meaning, are kept.

## Engine comparison and default routing

| Language | PaddleOCR CER | Tesseract CER | Default primary engine |
|---|---|---|---|
| en | 0.000 | 0.000 | PaddleOCR |
| hi | 0.007 | 0.000 | PaddleOCR |
| mr | 0.014 | 0.002 | PaddleOCR |
| te | 0.009 | 0.003 | PaddleOCR |
| ta | 0.008 | 0.000 | PaddleOCR |
| kn | 0.388 | 0.013 | **Tesseract** (`NGOCR_OCR_LANGUAGE_PRIMARY`) |

On this corpus Tesseract equals or beats PaddleOCR in every language and is about 3x faster.
**This does not by itself justify switching engines:** the corpus is clean renders of
standard fonts, the kind of input Tesseract's training data is built from. PaddleOCR's
learned detector is generally more robust on real scans, photographs and complex layouts,
and that is unmeasured. Kannada is switched because PaddleOCR is unusable for it. Other
languages stay PaddleOCR-primary until a real-document evaluation decides (OD-21).

## Not yet measured

Real scans, photographs, handwriting, tables/forms, mixed-language pages, confidence
calibration, field-level accuracy, and the effect of each preprocessing profile on OCR
accuracy (the harness supports `--preprocess <profile>`).
