# Performance

Measured CPU performance. Every figure states its hardware and inputs. **None of these were
measured on the target Ubuntu 24.04 server**; re-run on reference hardware before capacity
planning (Phase 10).

## How to reproduce

```bash
make bench                                            # all profiles, OpenCV default threads
.venv/bin/python scripts/benchmark_preprocessing.py --repeat 5 --threads 1
```

Inputs are synthetic A4 text pages at 300 DPI (`tests/fixtures/pages.py`): clean, skewed 3°,
rotated 90°, Gaussian noise σ=20, faded (ink 150 on 200), and shadowed (50% illumination
gradient). They contain no real documents, so results are reproducible but are not
representative of every real-world scan.

## Results, 2026-10-22 (Phase 3)

Hardware: Intel Core i5-7200U @ 2.50 GHz (2 cores / 4 threads, laptop), Ubuntu 26.04,
Python 3.11.17, OpenCV 4.14.0 (PyPI wheel), NumPy 2.4.6. 5 repetitions per sample.

### Per page, algorithms only (in-memory)

| Stage | 4 OpenCV threads: mean / p95 ms | 1 thread: mean / p95 ms |
|---|---|---|
| Quality assessment | 195 / 246 | 203 / 242 |
| Profile `standard` (flag-driven; 0 ms when nothing is flagged) | 168 / 302 | 177 / 344 |
| Profile `degraded_scan` | 335 / 442 | 366 / 402 |
| Profile `photo` | 349 / 453 | 394 / 494 |
| Profile `binarize` | 313 / 385 | 317 / 423 |

Profile times include the post-processing quality re-assessment (`quality_after`, about
190 ms) whenever a page was changed.

| Step, when applied | 4 threads: mean ms | 1 thread: mean ms |
|---|---|---|
| orientation (quarter turn) | 3 | 3 |
| deskew | 14 | 30 |
| illumination flattening | 11 | 14 |
| denoise (median 3) | 10 | 15 |
| threshold (adaptive Gaussian) | 79 | 78 |
| contrast (stretch + CLAHE) | 120 | 153 |

### End to end, 10-page scanned PDF at 300 DPI

Ingest (render, quality assessment, PNG encode, fsync), then preprocessing run, via the
services on local SSD:

| Profile | Ingest ms/page | Preprocess ms/page | Total ms/page |
|---|---|---|---|
| standard | 931 | 281 | 1,212 |
| degraded_scan | 908 | 770 | 1,679 |

## Optimisations made in Phase 3 (measured)

| Change | Before | After |
|---|---|---|
| Background-variation metric on a ~400 px copy instead of the analysis image | assessment 444 ms | 191 ms |
| Illumination background estimated at ~50 DPI, upscaled | 478 ms | 10 ms |
| PNG zlib level 3 instead of 6 for stored pages (lossless either way) | 671 ms per RGB page, 2.5 MB | 383 ms, 2.1 MB |
| End-to-end, `standard` profile | 1,902 ms/page | 1,212 ms/page |

## Observations and sizing notes

- Single-threaded OpenCV is only 5–15% slower: most time is single-threaded NumPy and PNG
  encoding. For throughput, run several worker processes with `NGOCR_OPENCV_THREADS=1`
  rather than one process with many threads (applies from Phase 4, when workers exist).
- At about 1.2 s/page on one core, 10,000 pages/day needs about 3.4 core-hours of
  ingest+preprocessing, before OCR. OCR (Phase 5) is expected to dominate. It is unmeasured.
- **Storage:** about 4–6 MB per scanned page at 300 DPI (RGB source PNG plus greyscale
  preprocessed PNG; noisy scans compress poorly). 10,000 pages/day is about 40–60 GB/day.
  A retention policy is needed (OD-16).
- Ingestion still holds the PDFium lock while assessing quality (ADR-0012), so PDFs are
  processed one at a time per process.

## OCR throughput (Phase 4, 2026-10-22)

Same laptop CPU (i5-7200U, 2 cores / 4 threads). PaddleOCR 3.7.0 on PaddlePaddle 3.2.2,
`PP-OCRv5_mobile_det` plus a mobile recogniser, one process, default threads, synthetic A4
pages with 16 text lines (`make eval`).

| Condition (page DPI) | Mean s/page |
|---|---|
| clean (300 DPI) | 6.15 |
| scan (200 DPI) | 3.89 |
| lowres (150 DPI) | 2.64 |
| all 36 pages | 4.23 (about 14 pages/min) |

Model loading: 6–11 s on the first call per language per process (excluded above).

PaddlePaddle backend and tuning, measured on one 200 DPI page with 39 lines:

| Configuration | s/page |
|---|---|
| PaddlePaddle 3.3.1, oneDNN | fails (`ConvertPirAttribute2RuntimeAttribute`) |
| PaddlePaddle 3.3.1 or 3.2.2, oneDNN off | 35–42 |
| **PaddlePaddle 3.2.2, oneDNN, 4 threads** | **6.3** |
| … default threads | 7.6 |
| … 1 thread | 11.8 |
| … recognition batch 16 (vs 6) | 10.4 |
| … detection input capped at 1280 px | 6.3 (no gain) |
| … plus text-line orientation model | 7.3 |

Almost all time is inside Paddle inference: the cProfile measurement showed 99.5% in
`paddle.base.libpaddle.run`. Recognition time scales with the number of text lines.

**Sizing implication (OD-19):** at about 4–6 s/page on this laptop, 10,000 pages/day needs
about 12–17 core-hours/day for OCR alone, which is several workers in parallel. This must be
re-measured on the reference server (AVX-512, more cores) before capacity planning.
### Tesseract

Tesseract 5.5.0, one process, default OpenMP threads, same 36 pages: **1.37 s/page mean
(about 44 pages/min)**, 0.9–1.9 s by language and condition. The first call per language
costs about 1.2–1.8 s (model load). That is about 3x PaddleOCR's throughput on this
hardware.
