# Structured Data Extraction

Turns a document's OCR and layout result into typed, validated fields and table records.
Every value carries the evidence it was read from. **Rules and templates only:** no LLM, no
model and no network call is on this path (ADR-0026; enforced by
`tests/unit/test_extraction_offline.py`).

- **Built-in document types**, with their fields, labels, tables and checks:
  [EXTRACTION_SCHEMAS.md](EXTRACTION_SCHEMAS.md) (generated).
- **JSON Schemas of the outputs:** [`docs/schemas/`](schemas/) (generated from the code and
  checked by the test suite).
- **Endpoints:** API_SPECIFICATION.md.

## Pipeline

```
stored layout run (+ its OCR run; embedded text for native PDF pages)
  → classification (templates, then weighted rules)
  → candidates per field (template region, label, section block, label table row, format scan)
  → normalisation (dates, amounts, identifiers, addresses …)
  → resolution: extracted | missing | ambiguous | conflict | invalid
  → tables → records (header words → columns; summary rows set aside)
  → Pydantic validation → consistency checks → review decision → stored result
```

`POST /api/v1/documents/{id}/extraction` runs it on a stored layout run. The result is
stored as `derived/<id>/extraction/<layout_run>.json`. Native-text PDF pages, which have no
layout, are read from their embedded text layer (`source: pdf_text`, coordinates in points);
tables are not available on those pages (OD-25).

## Classification

1. **Templates.** A schema may define templates (a known vendor layout). When all of a
   template's anchor phrases occur, the document is that type (`status: template`), and the
   template's regions locate fields.
2. **Rules.** Each schema scores the text with weighted keywords (any language), regular
   expressions (e.g. a GSTIN) and table header words. The best type must reach its
   `min_score` and score at least 1.5 times the runner-up. Otherwise the result is
   `ambiguous` or `unknown`, and nothing is extracted. Per-type scores and the matched
   evidence are in the result.
3. A caller can name the type (`document_type`), giving `status: requested`.

## Field candidates and resolution

| Method | Tier | Source |
|---|---|---|
| `template` | 0 | Lines inside the template's region for the field |
| `label` | 1 | Text after one of the field's labels on a line. With nothing after the label, the next line(s) in the same block. Labels must start the line; a second label on the same line counts only when a separator (`:` `-` `=` `#` `\|`) follows it. The longest label wins ("Grand Total" before "Total"). |
| `section` | 1 | Unlabelled lines under a section heading ("Seller:", "Bill To:"): the first line for a `block: first` field (party name), the following ones for `block: rest` (address) |
| `table_row` | 1 | A table row whose first cell starts with a label ("CGST 6%"); the value is the row's last cell |
| `pattern` | 2 | A GSTIN, PAN, IFSC or Aadhaar number found by its format, attributed by the section it is in |
| `unattributed` | 3 | A shared label ("GSTIN") or a format match outside any seller/buyer section. **Never chosen**: reported so a reviewer can see it |

Labels shared by several fields (e.g. `gstin` for seller and buyer) are resolved by the
nearest preceding section heading in the same layout region. A label followed by text with
no value of the field's type (e.g. "Permanent Account Number **Card**") is not a candidate.

A field is resolved from its strongest tier:

| Status | Meaning | `value` |
|---|---|---|
| `extracted` | Every attributed candidate agrees | The normalised value |
| `missing` | No candidate | `null`. **Never invented** |
| `ambiguous` | The text has several readings ("10 11800.00"; 03/04 with day-first off), or the field cannot be attributed | `null`; readings in `notes` and `candidates` |
| `conflict` | Different valid values, or a weaker candidate disagrees | `null`; all candidates listed |
| `invalid` | Found, but fails its format or checksum (GSTIN check character, Aadhaar Verhoeff, impossible date) | `null`; `raw` and `errors` kept |

Every field lists all `candidates` considered, with method, raw text, alternatives, errors
and evidence.

## Evidence and provenance

Each extracted value carries `evidence`: page number, source (`ocr_line`, `pdf_text`,
`table_cell`, `template_region`), the source text, box (`px` in the OCR image of the page, or
`pt` for PDF text), OCR line indices (into the stored OCR run's `OcrPage.lines`), layout
region / table / row / column, and OCR confidence. `confidence` on the field is the lowest
evidence confidence. Together with `ocr_run` and `layout_run`, every value traces back to the
pixels it was read from (the layout overlay endpoint shows them).

## Normalisation

| Type | Output | Rules |
|---|---|---|
| `date` | ISO `YYYY-MM-DD` | dd/mm/yyyy, dd-mm-yy, `14 March 2024`, `14-Mar-24`, `March 14, 2024`, ISO. Numeric dates are read **day-first** (`NGOCR_EXTRACTION_DAY_FIRST`, noted on the field); with it off, a date valid both ways is `ambiguous`. Impossible dates are invalid |
| `amount` | Decimal string, 2 places | ₹ / Rs. / INR; Indian (1,23,456.78) or international grouping, anything else invalid; percentages are not amounts; Cr/Dr noted, sign not inferred; exact decimal arithmetic |
| `number`, `percent` | Decimal string | quantities with units; rates 0–100 |
| `gstin` | 15 characters | Format, state code and GSTN mod-36 check character |
| `pan` | 10 characters | Format (4th letter = holder type) |
| `aadhaar` | `XXXX XXXX 1234` | 12 digits, not starting 0/1, Verhoeff check digit; **masked by default** |
| `ifsc` | 11 characters | Format |
| `identifier` | Text | The schema's pattern, else the first token |
| `name`, `text` | Text | Whitespace collapsed |
| `address` | Text | Lines joined with ", "; PIN code noted |
| `pincode`, `phone`, `gender` | Text | 6 digits; Indian mobile as +91…; male/female/transgender (also Hindi words) |

Digits in Indian scripts (Devanagari, Telugu, Tamil, Kannada, …) are converted to ASCII
before parsing.

## Tables to records

A schema table lists columns with header words. Each layout table maps header cells to
columns (exact match first, then the longest header word); a table qualifies only when every
required column is found. For each body row the result holds one record. Each cell is
normalised and has its own status (`extracted`, `missing`, `ambiguous`, `invalid`) and
evidence. Rows starting with a summary word ("Total", "Opening Balance") are kept apart in
`summary_rows`, and repeated header rows on continuation pages are skipped. Records from all
pages are concatenated in order. Tables the layout stage routed to review make the
extraction need review too.

## Validation and checks

- **Pydantic:** a record model is generated per schema (dates, decimals, constrained
  identifiers; required fields required). Every problem is a `ValidationIssue` with a code
  (`missing_required`, `invalid_value`, `field_ambiguous`, `field_conflict`,
  `field_invalid`, `cell_invalid`, …) and a location (`["fields", name]` or
  `["tables", table, index, column]`). `record` always lists every field, with `null` for
  what was not extracted.
- **Consistency checks**, with exact decimal arithmetic and a per-check tolerance (0.01 by
  default):
  - `row_product`: quantity × rate = amount.
  - `column_sum`: line amounts add up to the taxable value.
  - `field_sum`: grand total = taxable value + taxes + round-off.
  - `equal`: CGST = SGST.
  - `gst_tax_type`: same state means CGST + SGST; different states means IGST.
  - `gstin_pan`.
  - `date_order` and `not_future`.
  - `balance`: opening + credits − debits = closing.
  - `running_balance`.
  - `rows_within_period`.
- **How check results read:**
  - A check whose inputs are missing is `not_applicable`; a missing value is never treated as
    0. The exception is a schema's declared `optional_terms`; when such a term is counted as
    0 and the check fails, the message says so.
  - A failed check reports expected, actual and difference values, and the failing rows.

## Review

`review.needs_review` is set, with reasons, when any of these apply:
- the document type is `unknown` or `ambiguous`;
- a required field is missing;
- a field is ambiguous, in conflict or invalid;
- a required table is not found, or a table has invalid cells;
- a check fails;
- evidence has OCR confidence below `NGOCR_EXTRACTION_MIN_EVIDENCE_CONFIDENCE` (0.70);
- the OCR or layout stage flagged a page the values came from.

`status` is `complete` only when none of these hold and validation passes.

## Personal data

Fields marked `sensitive` are identified in results and exports. Aadhaar numbers are masked
to their last 4 digits in values, raw text, candidates and evidence, in the stored result and
in every export. Full numbers are kept only with `NGOCR_EXTRACTION_MASK_AADHAAR=false`.
**The OCR and layout runs still contain the page text, unmasked** (OD-30): protect
`data/derived/` accordingly. Exports are recorded in the audit log (`extraction.exported`),
without content.

## Export

`GET …/extraction/{run}/export?format=json|csv|xlsx[&table=name]`

| Format | Content |
|---|---|
| `json` | The complete result (same schema as the API response) |
| `csv` | Fields (one row per field: status, value, raw, method, confidence, pages, evidence, notes, errors), or one table's records with `table=`. UTF-8 with BOM (Excel opens Indic text correctly) |
| `xlsx` | Sheets: Summary, Fields, one per table, Checks, Validation. Amounts and quantities are numbers, dates are dates, identifiers stay text |

Missing values export as empty cells, never 0. **Formula injection is neutralised.** In CSV,
text cells starting with `= + - @`, tab or CR are prefixed with `'` (plain numbers such as
`-250.00` are not). In Excel, every text cell is stored as a string and never as a formula.

## Adding a document type

Write a JSON schema (format: `docs/schemas/document_schema.schema.json`; examples in
`src/newgen_ocr/extraction/schemas/`) into `NGOCR_EXTRACTION_SCHEMA_DIR`. It is validated at
startup, and any unknown field, column, section, invalid regex or broken check reference
stops the service. Measure the new type on real samples before relying on it.
