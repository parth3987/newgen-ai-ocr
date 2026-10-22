# Accuracy Evaluation

Status: **no measurements exist yet.** The harness arrives in Phase 4. This project makes
no accuracy claim (in particular no universal "99%") until it has measured one under the
rules below.

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
