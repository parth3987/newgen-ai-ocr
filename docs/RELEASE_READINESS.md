# Release Readiness: evaluation, targets and gates

Status: **implemented (Phase 13, ADR-0033)**. This document is for whoever approves a
release for production. It covers what is measured, on which documents, against which
targets, and what must pass. The latest results are in §6.

## 1. Workflow

| Where | Command | Produces |
|---|---|---|
| Staging | `make staging` (includes `make evaluate`) | Accuracy and coverage per cohort (`reports/evaluation/`), the gate evidence, the signed bundle |
| Production CPU server | `ngocrctl install …`, then `ngocrctl benchmark --documents 200 --clients N` | `<prefix>/state/benchmark-<stamp>.json`: throughput, latency, queue delay, failures, CPU, memory, and the host's identity |
| Staging | Declare the server in `evaluation/targets.json` (`reference_server`), then `make release-gates BENCHMARK=<file>` | `reports/release-gates.md` and `.json`: **APPROVED** or **NOT APPROVED**, gate by gate |

Production approval means `make release-gates` reports APPROVED for the committed release
being shipped.

## 2. Cohorts

Each cohort is defined in `evaluation/cohorts.json` with its role, kind, languages,
document types, conditions and metric families.
- **Tuning cohorts** are the sets the system was developed and calibrated on. Their results
  are never reported as final accuracy.
- **Evaluation cohorts** are used only for measurement.

| Required by the spec | Synthetic evaluation cohort | Real cohort (add data to measure) |
|---|---|---|
| Clean printed documents | `eval-clean-printed-text` (6 languages), `eval-clean-printed-documents` (5 document types) | `real-clean-printed-text`, `real-clean-printed-documents` |
| Poor-quality scans | `eval-poor-scans-text`, `eval-poor-scans-documents` (office scan at 200 DPI, heavy degradation at 150 DPI) | add one (evaluation/real/README.md) |
| Tables and complex layouts | `eval-tables` (random sizes, rulings and contents) | add one |
| Each supported printed language | `eval-printed-{en,hi,mr,te,ta,kn}` | `real-clean-printed-text` (per language) |
| Each supported handwriting script | `eval-handwriting-latin` (English, experimental), `eval-handwriting-devanagari` (unsupported: routing only) | `real-handwriting-telugu-tamil-kannada` (no offline font exists to synthesise them) |
| Mixed-language documents | `eval-mixed-language` (an Indian language with English lines, automatic mode) | add one |
| Business documents as received | n/a | `real-documents` |

**Tuning and evaluation never share documents.** The separation is enforced in four ways:
- **The evaluation run** hashes every evaluation document's file and normalised truth text,
  hashes every tuning cohort's documents too (692 today), and refuses to run on any match.
- **Seeds:** the registry refuses a generator seed used twice.
- **The tuning tool:** `calibrate_review.py --dataset` refuses evaluation folders.
- **Real data:** real tuning documents get their own tuning cohort and never go in an
  evaluation folder.

**Synthetic cohorts are not evidence about real documents.** Each states its independence
from tuning: new seeds, random word sequences rather than the tuning sentences, and fonts
the tuning corpus never rendered (DejaVu and Nimbus for English, the Bold weights of Noto
for the Indian scripts). They still share the generators' templates and vocabulary. They
check the measurement and catch regressions. The 99% targets are therefore defined on the
real cohorts.

## 3. Metrics

The definitions are in `newgen_ocr/evaluation/release_metrics.py`. Every proportion is
reported with its sample size and a 95% Wilson interval: 40 of 40 correct is reported as
"100%, 95% interval 91 to 100%", not as "100%".

| Metric | Definition |
|---|---|
| CER, WER | Micro-averaged: total edits over total reference characters or words |
| Character accuracy | 1 − CER |
| Field exact match | Of the fields whose truth has a value, the share extracted with exactly that value (whitespace and case normalised). Before review |
| Invented values | Values extracted where the truth has none (counted separately) |
| Document exact match | Right document type and every field right |
| Table structure | Exact grid (rows, columns, spans); mean adjacency F1; cell CER |
| **Auto-accept precision** | Of the values the review gate accepts without a person (shipped calibration), the share that are correct |
| **Automatic-processing coverage** | The share of all extracted values accepted without a person. Always reported with the precision |
| Document auto-accept rate, manual-review rate | Documents with every value and table verified, and the rest |
| Latency p50/p95, pages per minute | Per page, one at a time (evaluation run); or end to end through the service (benchmark) |
| Queue delay | `started_at − created_at` of each job (benchmark) |
| Failure rate | Failed or timed-out documents and jobs |
| CPU and memory | Process CPU seconds per page and peak memory (evaluation run); each container's CPU % and peak memory, sampled every 5 s (benchmark) |

Every report records the dataset size, languages, document types, conditions, data kind,
independence and environment (CPU model, cores, memory, OS, version, commit, the model
lock's hash and the calibration). It has slices by language, condition and document type,
and every document's row.

## 4. Targets

Each target in `evaluation/targets.json` is tied to one metric and one cohort.

| Target | Metric and cohort | Binding |
|---|---|---|
| Auto-accept precision ≥ 99%, at least 300 accepted values; coverage and manual-review rate reported beside it | `review.auto_accept_precision` on `real-documents` | **Provisional:** binding once the real cohort has data (owner, 2026-10-24) |
| Field exact match ≥ 99%, at least 300 values | `fields.field_exact_match` on `real-clean-printed-documents` | Provisional, as above |
| Character accuracy ≥ 99% in every language, at least 5,000 reference characters each | `text.character_accuracy` per language on `real-clean-printed-text` | Provisional, as above |
| ≥ 6.95 pages per minute (10,000 pages a day; OD-19, to confirm) | `benchmark.pages_per_minute`, on the declared reference server only | Always |
| No failed jobs | `benchmark.failure_rate`, on the reference server only | Always |
| Every English handwriting page routed to review | `eval-handwriting-latin` | Always |
| Every Hindi and Marathi handwriting page routed to review | `eval-handwriting-devanagari` | Always |
| No document fails in the evaluation | `performance.failures` on every measured cohort | Always |

Synthetic counterparts are shown next to each provisional target in the gate report,
marked *not evidence*.

## 5. Release gates

`make release-gates` (`scripts/release_gates.py`) approves production only when every
blocking gate passes:
1. **Staging evidence for this image's content ID:** the tests passed, Trivy found no
   fixable HIGH or CRITICAL, the SBOMs exist, and the offline network test passed.
2. **The disconnected install rehearsal passed.**
3. **Separation:** no evaluation document is a tuning document.
4. **Freshness:** the evaluation ran complete, on this commit, from a clean tree.
5. **Reporting:** every measured cohort states its size, languages, document types and
   conditions, and every auto-accept result comes with its coverage.
6. **Every target, by its binding:**
   - an always-binding target that is not measured **blocks**;
   - a provisional target is reported as *not measured*, non-blocking, until its real
     cohort has data. From then on it blocks unless it is met on enough samples.

## 6. Results (2026-10-24, version 0.13.0, image `1455bec04023`)

**Release gates: NOT APPROVED.** That is the correct verdict today:
- **Blocking:** the two performance gates are *not measured*, because no reference server is
  declared (OD-53).
- **Not measured, non-blocking:** the three provisional 99% targets, which wait for real
  documents (OD-55).
- **Passed, everything else:** the staging evidence for this image (677 tests passed, 0
  skipped; 0 fixable HIGH or CRITICAL findings; the offline test), the install rehearsal
  (including the benchmark), separation (171 evaluation documents against 692 tuning
  documents, no overlap), freshness, reporting, handwriting routing (8 of 8 pages), and 0
  failed documents.
- **Caveat:** this run used `--allow-dirty` (Phase 13 is not committed yet). A release needs
  the run repeated on the committed tree.

**Accuracy on the synthetic evaluation cohorts.** These check the machinery; they are not
evidence about real documents (§2). Environment: Intel Core i5-7200U (4 threads), 31 GB,
in-process, one page at a time.

| Cohort | Size | Languages · conditions | Result |
|---|---|---|---|
| Clean printed text | 24 pages, 10,411 chars | 6 · clean | CER 1.11%, WER 7.61%. Per language: en 0.00%, hi 0.36%, ta 1.02%, mr 1.11%, te 1.61%, kn 2.13% |
| Poor scans, text | 24 pages, 10,482 chars | 6 · scan, heavy | CER 1.43% (scan 0.95%, heavy 1.89%). Per language: en 0.07%, te 0.59%, hi 0.82%, ta 1.43%, kn 1.92%, mr 3.45% |
| Printed, per language (separate cohorts) | 6 pages each | clean, scan | CER: en 0.18%, kn 0.55%, te 0.77%, hi 0.82%, ta 0.82%, mr 1.29% |
| Mixed language | 12 pages, 5,010 chars | hi, te, ta, kn with English · auto | CER 0.56%, WER 4.30% |
| Clean printed documents | 25 documents, 173 values | en, hi · clean | Field exact match 100% (95% CI 97.8–100%); document exact match 100% (25/25; CI 86.7–100%); 0 invented values. **Auto-accept precision 100% (161/161; CI 97.7–100%), coverage 93.1% (161/173)**; manual-review rate 76% of documents (19/25: one unverified value sends the whole document to review) |
| Poor scans, documents | 30 documents, 209 values | en, hi · scan, heavy | Field exact match 99.5% (208/209; CI 97.3–99.9%); document exact match 96.7% (29/30); 0 invented. **Auto-accept precision 100% (90/90; CI 95.9–100%), coverage 43.1%** (scan 88%, heavy 0%: heavy pages all go to a person); manual-review rate 86.7% |
| Tables | 12 tables | en · clean, scan | Exact structure 75% (9/12; CI 46.8–91.1%): clean 83%, scan 67%. Adjacency F1 0.88 |
| English handwriting (font proxy) | 4 pages | en | CER 5.69%; 4/4 routed to review |
| Hindi and Marathi handwriting (font proxy) | 4 pages | hi, mr | Unsupported: nothing read, 4/4 routed to review |
| Real cohorts (4) | none | n/a | Not measured: no real documents yet |

What the synthetic figures already show, before any real data:
- **Character accuracy.** The 99% target would fail today for Kannada, Telugu, Marathi and
  Tamil even on clean synthetic pages, at 1.0 to 2.1% CER in fonts the system was not
  tuned on. English and Hindi meet it.
- **Auto-accept.** It made no error in 251 accepted values. Its coverage drops sharply on
  degraded scans, and the manual-review rate is high, because review is decided per
  document.
- **Tables:** a quarter of the random tables had a structure error.
- **A defect fixed:** the first run found a sparse handwriting page skipped as blank and
  not routed to review (OD-56).

**Performance on the staging laptop.** It is not the reference server, so this does not
count for the targets.

| Measurement | Result |
|---|---|
| In-process, per page (one at a time) | Across cohorts (excluding unsupported handwriting, where nothing is read): p50 2.9–9.5 s, p95 3.6–13.3 s; 6.1–20.2 pages per minute; CPU 4.6–33 s per page (several threads). Peak memory 3.8 GB |
| Service benchmark: install rehearsal, 1 worker process, 16 jobs, 2 clients | **6.55 pages per minute**. End-to-end p50 18.3 s, p95 21.4 s. Queue delay p50 8.3 s, p95 10.9 s. Processing p50 9.3 s, p95 11.6 s. Failure rate 0. Worker CPU 82% mean (112% peak), 2.4 GB peak; API 10%, 172 MB |

## 7. To approve a release

1. **Real documents.** Put consented documents with ground truth in `evaluation/real/`
   (README there), at least the sizes the targets require. Tuning documents go in a
   separate tuning cohort.
2. **The reference server.** Declare it in `evaluation/targets.json` (`reference_server`),
   and confirm or change the throughput target (OD-54).
3. **Staging.** Commit the release, then run `make staging`. It runs the evaluation and
   builds the bundle on a clean tree.
4. **The server.** Install the bundle there, then run `ngocrctl benchmark`. Bring back the
   report.
5. **Approval.** `make release-gates BENCHMARK=<report>`. It must say APPROVED.
