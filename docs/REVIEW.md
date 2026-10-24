# Verification and Human Review

Every extraction result is checked field by field and placed in a review queue. A value is
**verified** only when it passes every gate below. A document can be accepted without a
person only when automatic acceptance is switched on, a calibration file is loaded, and
every one of its values and tables is verified. **OCR confidence alone never verifies a
value** (ADR-0028).

- **Endpoints:** API_SPECIFICATION.md, "Verification and review".
- **JSON Schemas:** `docs/schemas/review_item.schema.json`, `reviewed_result`,
  `correction_request`, `review_history_event`, `calibration` (generated; drift-checked).
- **Tokens and roles:** below, and ADR-0027.
- **Calibration results:** ACCURACY_EVALUATION.md, "Review thresholds".

## Gates for each value

| # | Gate | Fails with |
|---|---|---|
| 1 | The value was extracted: not missing, ambiguous, in conflict or invalid | `required_missing`, `ambiguous`, `conflict`, `invalid` |
| 2 | The document type is reliable (classified, template or requested) | `document_type` |
| 3 | OCR and layout did not route the evidence page to review | `source_flagged` |
| 4 | No business-rule check involving the value failed | `check_failed` |
| 5 | The second OCR engine does not read a different value in the same place | `engine_disagreement` |
| 6 | The value's OCR confidence reaches the threshold **calibrated for that field** on held-out ground truth | `not_calibrated`, `below_threshold`, `no_confidence` |

Notes on the gates:
- **Optional fields reported missing** are verified only when "missing" judgements for that
  field were calibrated as reliable. Otherwise the reason is `missing_unverified`.
- **Gate 3, source pages.** Page reasons from OCR (low quality, script problems, unverified
  handwriting) and from layout (overlapping regions, unsupported structures) flag every
  value on the page. Table-structure reasons flag only the table. Two page reasons are
  judged for each value instead:
  - engine disagreement on the page (gate 5 replaces it);
  - low-confidence lines elsewhere on the page (gate 6 uses the value's own confidence).
- **Gate 4, check participation.** A check involves the fields it names: the target,
  terms, optional and negative terms, and listed fields. When `grand_total` fails, the
  taxable value and the taxes are flagged too.
- **Gate 5, engine cross-check.** It needs OCR run with `"compare": true`. The second
  engine's words inside the evidence box are normalised with the field's own rules:
  - **agree:** one of its readings is the value;
  - **disagree:** it read text there, but never this value;
  - **no reading:** it read nothing there. This is recorded but does not block, because
    Tesseract often reads nothing inside ruled tables;
  - **unavailable:** there is no second run, the second engine read a different image, or
    the evidence is PDF text.

  Results from the two engines are never merged (ADR-0022).

**Tables** are verified when all of these hold:
- no cell is ambiguous or invalid;
- the layout reported no issue with the table and its page is not flagged;
- every applicable check over the table passes, and at least one does (otherwise nothing
  corroborates the rows);
- no cell disagrees with the second engine, except cells in columns that a passing
  row-level check confirms on every row (quantity × rate = amount; running balance).

**Documents** are eligible for automatic acceptance when all of these hold:
- every field and table is verified;
- validation passes and no check fails;
- the document type is reliable.

## Calibration

`scripts/calibrate_review.py` (`make calibrate`) runs labelled documents through the whole
offline pipeline, with the second engine. It then assesses each document *without*
thresholds. Each field becomes an observation with three parts:
- the value's confidence;
- whether it passed every other gate;
- whether it equals the ground truth.

Documents are split into two parts:
- **Calibration split:** thresholds are fitted on it.
- **Test split:** thresholds are only measured on it, never chosen from it.

Fitting, for each field:
- **Threshold.** The threshold is the lowest confidence at which the gated values at or
  above it are correct with a **one-sided 95% Wilson lower bound ≥ 0.95**, over **at least
  50** values. Defaults: `--target`, `--confidence`, `--min-samples`.
- **Too little data.** A field with too little data falls back to its **field type**
  pooled across document types. This happens only if none of the field's own wrong values
  lies at or above the pooled threshold.
- **No threshold.** A field without a threshold is never verified.
- **Missing values.** "Missing" judgements are calibrated the same way, without a
  confidence.

The calibration file records its dataset (kind `synthetic`, `real` or `reviews`), schema
versions, per-entry statistics and reliability bins, and the test-split results.
Thresholds bind to their sources:
- **Content id.** The file's id is a hash of its content, and a hand-edited threshold is
  refused at startup.
- **Schema version.** A schema whose version differs from the calibrated one has no
  thresholds.

Sources of ground truth:

| Source | Command | Notes |
|---|---|---|
| Synthetic set (default) | `make calibrate` | Seeded, varied documents of all five types, including heavily degraded low-DPI scans (`newgen_ocr.evaluation.calibration_corpus`). Shows the method; **not for production** |
| Real labelled documents | `--dataset DIR` | `manifest.json` + images; see the script's docstring for the format. Must be consented, with Aadhaar masked in the truth |
| Approved reviews | `--from-reviews DATA_DIR` | The approved value is the truth for what was extracted. No OCR re-run. Covers only documents that were reviewed; while auto-acceptance is off that is every document |

**Automatic acceptance** (`NGOCR_REVIEW_AUTO_ACCEPT=true`):
- It requires `NGOCR_REVIEW_CALIBRATION_FILE`.
- In production it refuses a synthetic calibration unless
  `NGOCR_REVIEW_ALLOW_SYNTHETIC_CALIBRATION=true` is set knowingly.
- It is off by default: every document waits for a person, and verified values are only
  marked, so reviewers can concentrate on the rest.

## Review queue and workflow

One review item exists per extraction result:
- `data/review/items/<document>/<layout_run>.json`
- Queue order: priority first (classification problems, missing required fields, failed
  checks, conflicts and engine disagreements weigh most), then age.
- Re-extracting a document creates a new **revision**. The earlier revision is kept,
  read-only, and its corrections are not carried over (the values may have changed).

| Step | Who | Rules |
|---|---|---|
| Claim / release | reviewer | One assignee at a time; an admin may take over with `force` |
| Correct | the assignee | See "Correct" below |
| Approve | approver, admin | See "Approve" below |
| Reject | reviewer | A reason code (`illegible`, `wrong_document_type`, `unsupported_document`, `incomplete_document`, `duplicate`, `other` + comment) |

**Correct.**
- `set` a value as printed: it is normalised and validated like an extracted one, so an
  impossible date or a bad GSTIN check character is refused.
- `clear` it (not on the page).
- `confirm` it, for a field or a whole table.
- `set` and `clear` need a reason code; `other` also needs a comment.
- A request is all-or-nothing.

**Approve.**
- Nothing may be left undecided (`unresolved` in the result).
- The corrected record must validate.
- Checks that still fail must be acknowledged by id, with a comment.
- **Four eyes:** the approver must not have corrected or confirmed values on the item
  (`NGOCR_REVIEW_REQUIRE_SECOND_PERSON`, default on).

Every change carries the item's `expected_version`. A stale version is refused with
`409 REVIEW_CONFLICT`, so two people cannot overwrite each other.

`GET …/result` returns the record with corrections applied, re-validated and re-checked.
Each value says where it came from: `verified`, `extracted`, `confirmed`, `corrected` (with
who), `cleared` or `unresolved`.

### Correction reason codes

| Code | Meaning |
|---|---|
| `confirmed` | The extracted value is right |
| `ocr_misread` | Characters were read wrongly |
| `wrong_value_selected` | Another text on the page was taken |
| `missed_value` | On the page, but not extracted |
| `value_not_present` | Extracted, but not on the page |
| `ambiguity_resolved` / `conflict_resolved` | A person chose between readings or candidates |
| `normalisation_error` | Read right, converted wrongly |
| `other` | Comment required |

## History and audit

Every step is appended to `data/review/history.jsonl`. Each entry records:
- sequence number, time, actor, role and request id;
- the location, extracted value, status, raw text, confidence and method;
- the previous and new value, the text typed, the reason code and the comment;
- the gate outcome at the time: verified, engine status, threshold and reasons.

Events are **hash-chained**: each carries the previous event's SHA-256 and its own.
`GET /api/v1/review/history/verify` (admin) reports the first broken link. A chain detects
edits, removals and reordering. It cannot stop someone with write access from rewriting the
whole file, so copy it off the host (OD-34). The history holds document values. Aadhaar
numbers are masked in it, and it belongs with `data/derived/` for access control.

The audit log (`audit/audit.jsonl`) records each step **without content**. It covers:
- `review.queued`, `review.auto_accepted`, `review.claimed`, `review.released`,
  `review.corrected`, `review.approved`, `review.rejected`;
- `auth.failed` (reason and the token's public id, never the token);
- `auth.denied`.

## Source crops and the review page

| Endpoint | Returns |
|---|---|
| `GET …/crop?field=` (or `table=&record=&column=`, or `candidate=`) | The evidence area, cut from the exact image the OCR engine read, with the evidence boxed |
| `GET …/pages/{n}?field=` | The page with the value's evidence in red and other candidates in blue |

When Aadhaar masking is on, every Aadhaar number the OCR read on the page is blacked out in
both. This is best effort: digits the OCR did not read cannot be located (OD-30).

Reviewers work in the web application (ADR-0030):
- **Queue:** `/app/review`, with views for open items, items assigned to me, pending,
  approved and rejected, and a reason filter.
- **Item:** `/app/review/<document_id>/<layout_run>`. It shows:
  - the source crop next to each value, with the gate results (verified, the reasons,
    confidence against threshold, the second engine's reading);
  - flagged table cells with their crops;
  - checks and history;
  - the page with the selected field's evidence highlighted.
- **Actions:** claim, release, correct (set, not on page, correct as is), confirm a table,
  approve (acknowledging failing checks) and reject.
- **Sign-in:** password sign-in with a session cookie, sent with images and API calls
  alike. It replaces the Phase 8 token page; `/review` redirects to `/app/review`.

## Correction patterns

`GET /api/v1/review/patterns` (approver) aggregates the history:
- per field: values reviewed, values changed, change rate and top reason;
- per reason, and per field type;
- **character confusions** in `ocr_misread` corrections ("5" read for "S", …);
- **verified but corrected:** values that passed every gate and were still changed by a
  person. These are calibration failures and should be investigated first.

Use them to:
- add labels or rules (`missed_value`, `wrong_value_selected`);
- fix normalisers (`normalisation_error`);
- refit thresholds from approved reviews (`--from-reviews`).

## Tokens and roles (ADR-0027)

```
python -m newgen_ocr.auth create --user asha --role reviewer [--expires-days 90]
python -m newgen_ocr.auth list
python -m newgen_ocr.auth revoke <token-id>
```

How tokens work:
- **Format:** `ngocr_<id>_<secret>`, sent as `Authorization: Bearer …`. The token is shown
  once; only a SHA-256 hash of its 256-bit secret is stored.
- **Storage:** in service mode, the `api_tokens` table in PostgreSQL. In local mode,
  `NGOCR_AUTH_TOKENS_FILE` (default `data/auth/tokens.json`, mode 0600), re-read when it
  changes. Either way revocation takes effect immediately.
- **Roles:**
  - `submitter` (integrations) uploads and reads or re-runs only its own documents and
    jobs. It has no access to the review queue;
  - `viewer` reads every document and result, the queue, items, crops and history;
  - `reviewer` also claims, corrects and rejects;
  - `approver` also approves and reads patterns;
  - `admin` also takes over items and verifies the history chain.

Every endpoint except the health probes and the static review page requires a token
(ADR-0029). In service mode the review items, revisions and history are stored in
PostgreSQL, with the same hash chain; the history table is append-only.
