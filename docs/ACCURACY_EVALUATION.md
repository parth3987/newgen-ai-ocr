# Accuracy Evaluation

Status: **synthetic baseline measured (Phases 4–5, 2026-10-22/23). No real-document or real-handwriting
measurements exist yet.** This project makes no accuracy claim (in particular no universal "99%") beyond
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
before Phase 8 (extraction) sign-off and recorded here. Fields that miss their threshold must route to
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

## Script, orientation and language identification (Phase 5, 2026-10-23)

`make eval-languages` (`scripts/evaluate_language_id.py`); raw data `reports/language_id.json`.

| Measure | Result |
|---|---|
| Script + orientation, 36 corpus pages × 0°/180° (+ 90°/270° on scan pages): 96 cases | **92/96 (95.8%)**. All 4 misses are sideways Marathi scans whose orientation OSD could not determine; they end `script_uncertain` (routed to review), not misrouted |
| OSD-only script answer on the same pages | Also 95.8% at this text size, but unreliable for Devanagari at other sizes (150 DPI: Marathi → "Cyrillic"; confidence 0.4 to 47). Hence the census (ADR-0023) |
| Unsupported scripts (Bengali, Gujarati, Arabic, Russian) | **0/4 routed to a supported recogniser**. Gujarati → uncertain; the others named (Bengali was named "Arabic" by OSD in one 150 DPI test, still routed to review) |
| Blank page | `undetermined` (review), never guessed |
| Hindi vs Marathi from OCR text, both engines, 12 pages | **24/24** correct, 0 uncertain. Caveat: the corpus text and the function-word lists were written by the same author; real-text accuracy is unmeasured |
| Detection cost | 4.0 s/page mean (OSD + census) on the laptop CPU |

### Mixed-script pages

| Page | Detected | Routed languages | Engine | CER |
|---|---|---|---|---|
| hi, Hindi-dominant + English | Devanagari (+Latin) | hi, mr, en | paddle | 0.009 |
| hi, English-dominant + Hindi | Latin (+Devanagari) | hi, mr, en | paddle | 0.002 |
| mr, Marathi-dominant + English | Devanagari (+Latin) | hi, mr, en | paddle | 0.007 |
| mr, English-dominant + Marathi | Latin (+Devanagari) | hi, mr, en | paddle | 0.002 |
| te, Telugu-dominant + English | Telugu (+Latin) | te, en | paddle | 0.006 |
| te, English-dominant + Telugu | Latin (+Telugu) | te, en | paddle | 0.005 |
| ta, Tamil-dominant + English | Tamil (+Latin) | ta, en | paddle | 0.002 |
| ta, English-dominant + Tamil | Latin (+Tamil) | ta, en | paddle | 0.002 |
| kn, Kannada-dominant + English | Kannada (+Latin) | kn, en | tesseract | 0.000 |
| kn, English-dominant + Kannada | Latin (+Kannada) | kn, en | tesseract | 0.000 |

Before the census fix, Devanagari-dominant mixed pages scored 0.595 Devanagari / 0.405 Latin
and were rejected as uncertain. The mixed-coverage rule (ADR-0023) was added for them.

## Handwriting (Phase 5, 2026-10-23): synthetic proxy only

`make eval-handwriting` (`scripts/evaluate_handwriting.py`); raw data `reports/handwriting.json`.
English corpus lines (8 per page) rendered in 5 OFL handwriting-style fonts (Caveat, Indie
Flower, Patrick Hand, Shadows Into Light, Kalam) at 200 and 300 DPI: 10 pages per engine.
**Font renders are far easier than real handwriting; these numbers cannot verify support.**

| Language | Engine | Pages | CER mean | CER worst | WER mean |
|---|---|---|---|---|---|
| en | paddle (printed recogniser) | 10 | 0.005 | 0.014 | 0.024 |
| en | **paddle_handwriting** (`PP-OCRv5_server_rec`) | 10 | 0.023 | **0.176** | 0.097 |
| en | tesseract | 10 | 0.029 | 0.088 | 0.104 |
| hi (Kalam Devanagari; informational, unsupported) | paddle | 2 | 0.029 | 0.039 | 0.125 |
| hi (informational, unsupported) | tesseract | 2 | 0.036 | 0.039 | 0.125 |

Finding: on Patrick Hand at 300 DPI the handwriting recogniser dropped almost every word
space (WER 0.63). It is a Chinese/English model, and Chinese has no word spaces. On this proxy it
is not better than the printed recogniser. Its value on *real* handwriting is unmeasured
(OD-22, OD-23). Real samples: see `evaluation/handwriting/README.md`.

## Layout, tables and key-value pairs (Phase 6, 2026-10-23): synthetic

**Test set** (`newgen_ocr.evaluation.layout_corpus`, generated by `make eval-layout`): 15 page
designs rendered clean (300 DPI) and as degraded scans (200 DPI, 1.2° skew, blur, noise,
JPEG), so 30 pages with exact ground truth.
- 13 tables: fully ruled simple, colspan header, rowspan, two-level header (rowspan + colspan),
  header-rule only, unruled, two-column field/value, plus one simple table each in Hindi,
  Marathi, Telugu, Tamil and Kannada and a Hindi colspan table.
- One two-column page with header, title, six paragraphs and a page number.
- One key-value page with five true pairs and three lookalikes (a time, a URL, plain text).

Full pipeline through the API: ingest, `standard` preprocessing, OCR with the default
engines, then layout. Ground truth is shifted into the deskewed (enlarged) OCR frame. Metrics
(`newgen_ocr.evaluation.layout_metrics`):
- **Association F1:** the ICDAR-2013 adjacency relations between true cells, with predicted
  cells matched by position. It is OCR-independent.
- **Content F1:** the same relations keyed by cell text, so any OCR error in either cell
  breaks the relation.
- **Cell CER:** character error rate of the cell text.

These are synthetic results and do not predict accuracy on real documents (OD-27).

| Layout model | Condition | Tables | Detected | Structure exact | **Wrong structure returned without review** | Association F1 | Content F1 | Cell CER | Tables routed to review |
|---|---|---|---|---|---|---|---|---|---|
| PP-DocLayout_plus-L (default) | clean | 13 | 12 | 12 | **0** | 0.923 | 0.828 | 0.103 | 1 |
| PP-DocLayout_plus-L (default) | scan | 13 | 12 | 11 | **0** | 0.881 | 0.800 | 0.142 | 2 |
| PP-DocLayout-M | clean | 13 | 12 | 12 | **0** | 0.923 | 0.828 | 0.103 | 1 |
| PP-DocLayout-M | scan | 13 | 12 | 12 | **0** | 0.923 | 0.860 | 0.097 | 1 |

Means include the undetected unruled table as 0 (association) and 1.0 (CER). Per table, with
the default model:
- **Row and column associations are preserved on 23 of the 24 detected tables** (association
  F1 1.000); the 24th was routed to review (below), so no table with wrong associations was
  returned as OK. All 10 wired tables per condition,
  including colspan, rowspan and the two-level header, have exact structure and are
  `verified` against their ruling lines.
- **The one structure error was caught.** On the degraded scan, SLANet split the header-rule
  (wireless) table into 3 columns instead of 4. The OCR-line evidence check found two lines
  side by side in one cell and boundaries cutting through words, and routed the table to
  review as `table_structure_mismatch`.
- **Unruled tables are not detected** (0 of 2). Their text is returned as text regions, read
  row by row, and the page is routed to review (`unclassified_text`; OD-26). No table is
  invented.
- **Kannada:** Tesseract's page OCR skips text inside ruled grids. Re-reading empty cells from
  their crops recovered text for 11 of 12 cells on the clean page and 8 of 12 on the scan
  (cell CER 0.21 / 0.22, including misreadings in recovered cells). The remaining cells (1 and
  4) are flagged `table_cell_unread`, never silently empty.
- **Other Indian scripts:** structure is exact. The content-F1 gaps are OCR misreadings of
  single cells (cell CER 0.015 to 0.045).
- **Pages:**
  - Reading order on the two-column page: pairwise accuracy 1.0 (column by column), clean and
    scan.
  - Headers and page number: recall and precision 1.0.
  - Column count: 2 (correct).
  - Key-value pairs: precision and recall 1.0 on both key-value pages (no lookalike became a
    pair) and on both field/value tables.

**PP-DocLayout-M:** table results are equal, and slightly better on the scan, because its
tighter table box led SLANet to the right structure. It misses titles and some text blocks,
however, so 29 of 30 pages carry `unclassified_text` against 7 of 30 with the default, and it
found 2 of 3 page-furniture blocks. It is 4.9x faster (median 0.56 s against 2.86 s of layout
per page). Keep `PP-DocLayout_plus-L` as the default; M is a throughput option that sends more
pages to review.
