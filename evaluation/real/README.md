# Real evaluation documents (not committed)

Release targets such as 99% auto-accept precision can only be shown on real documents
(ADR-0033). The synthetic cohorts check that the measurement works, not how the system
does on your documents. Real documents stay on the staging machine. Everything in this
folder except this file is git-ignored.

## Folders

Each real cohort in `evaluation/cohorts.json` reads one folder here:

| Folder | Cohort | What goes in it |
|---|---|---|
| `clean-printed-documents/` | `real-clean-printed-documents` | Clean printed business documents (invoices, receipts, statements, PAN, Aadhaar) |
| `clean-printed-text/` | `real-clean-printed-text` | Clean printed pages with exact transcriptions, every language |
| `documents/` | `real-documents` | Business documents as received, any quality. The auto-accept target uses this one |
| `handwriting-south/` | `real-handwriting-telugu-tamil-kannada` | Telugu, Tamil and Kannada handwriting |

To add a cohort, such as poor scans or tables, add an entry with `"kind": "real"` to
`evaluation/cohorts.json` and create its folder.

## One document

Each document is two files, `<id>.<png|jpg|tif|pdf>` and `<id>.truth.json`, for example:

```json
{
  "language": "en",
  "languages": ["en"],
  "document_type": "gst_invoice",
  "condition": "clean",
  "text": "optional: the exact transcription, one line per printed line",
  "fields": {
    "invoice_number": "INV-0042",
    "seller_gstin": "27AAACN1234A1Z7",
    "grand_total": "11800.00",
    "due_date": null
  },
  "tables": []
}
```

- **`fields`** use the schema's field names (docs/EXTRACTION_SCHEMAS.md) and normalised
  values: dates as `YYYY-MM-DD`, amounts with two decimals. `null` means the document
  really has no such value; extracting one anyway counts as *invented*.
- **`text`** is needed for CER and WER. Include it on text cohorts.
- **`handwriting: true`** marks a handwritten page.

## Rules

- **Consent:** only documents you are authorised to use for internal evaluation. Remove or
  mask personal data you do not need.
- **Keep tuning and evaluation documents apart.** A document used to write a rule, choose
  a setting or fit thresholds (`scripts/calibrate_review.py --dataset`) is a **tuning**
  document. Register its folder as a tuning cohort and never copy it here.
  `calibrate_review.py` refuses these evaluation folders. `scripts/evaluate_release.py`
  refuses any evaluation document that matches a tuning one, by file or by transcription.
- **Ground truth is entered once and checked by a second person.**
- **Size:** the targets name their minimum samples. For example, auto-accept precision
  needs at least 300 accepted field values, and character accuracy needs at least 5,000
  reference characters per language. Plan for several hundred documents.
