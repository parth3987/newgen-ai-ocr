# Model Inventory

Generated from `models.lock.json` and `evaluation/fonts.lock.json` (the sources of truth) on
2026-10-23. Code, weights and training-data terms are recorded **independently** per model.

## Policy

- Models are fetched only at staging, by `make models` (`scripts/fetch_models.py`), from the
  exact pinned revision. Every file's SHA-256 is recorded and cross-checked against an
  independent publisher hash: the Hugging Face LFS SHA-256 for Paddle weights, the Hugging Face
  git-blob SHA-1 for Paddle configs (`inference.json`/`.yml`, re-verified for every Paddle model
  on 2026-10-23), and the GitHub git-blob SHA-1 for `tessdata_best` and fonts.
- At runtime the application only *reads* `models/`. Each model is hash-verified before first
  use (`MODEL_INTEGRITY_ERROR` on mismatch). Missing models report `MODEL_NOT_AVAILABLE` and are
  never downloaded (ADR-0020/0021).
- **Training data:** neither PaddlePaddle nor the Tesseract project discloses training-data
  sources or terms. The owner accepted this risk on 2026-10-22 (ADR-0021).
- A model being pinned does not mean it is enabled. Enablement per language is decided by
  measurement in the language matrix (LANGUAGE_SUPPORT.md).

## Runtime models

Code licence: PaddleOCR/PaddleX/PaddlePaddle and Tesseract are Apache-2.0.

| Model | Engine | Kind | Languages | Source @ revision | Files (SHA-256) | Code licence | Weights licence | Training data |
|---|---|---|---|---|---|---|---|---|
| `paddle/PP-OCRv5_mobile_det` | paddleocr | detection | * | [PaddlePaddle/PP-OCRv5_mobile_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) @ `0d63e78e2b68` | `inference.json` `05feef1acb00aa4c…`<br>`inference.pdiparams` `afa1820cb16c1fd0…`<br>`inference.yml` `98069072e1b6b37d…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-OCRv5_server_det` | paddleocr | detection | * | [PaddlePaddle/PP-OCRv5_server_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det) @ `ca867c897ecb` | `inference.json` `af5876933d8806a1…`<br>`inference.pdiparams` `183146fe9d991035…`<br>`inference.yml` `28fb721efc3634fc…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-LCNet_x1_0_textline_ori` | paddleocr | textline_orientation | * | [PaddlePaddle/PP-LCNet_x1_0_textline_ori](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_textline_ori) @ `cd237a44b0e3` | `inference.json` `b81929eeaff8e52d…`<br>`inference.pdiparams` `0de2bcf996cf553e…`<br>`inference.yml` `8d5120d0e1a30a9d…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/en_PP-OCRv5_mobile_rec` | paddleocr | recognition | en | [PaddlePaddle/en_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/en_PP-OCRv5_mobile_rec) @ `267c36e24c33` | `inference.json` `fd1b6ec722ea841a…`<br>`inference.pdiparams` `3ec8a97ed6cefe85…`<br>`inference.yml` `27e91d0582f40168…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/devanagari_PP-OCRv5_mobile_rec` | paddleocr | recognition | hi, mr | [PaddlePaddle/devanagari_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/devanagari_PP-OCRv5_mobile_rec) @ `99dcce6d196b` | `inference.json` `20d3b121184ec6b7…`<br>`inference.pdiparams` `719be7d20bfe9530…`<br>`inference.yml` `9bd172dd26440c8c…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/te_PP-OCRv5_mobile_rec` | paddleocr | recognition | te | [PaddlePaddle/te_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/te_PP-OCRv5_mobile_rec) @ `151ab3b1c2f2` | `inference.json` `ad866e22872fbc0e…`<br>`inference.pdiparams` `45967d00d6b4af59…`<br>`inference.yml` `acebbe53f1831bf2…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/ta_PP-OCRv5_mobile_rec` | paddleocr | recognition | ta | [PaddlePaddle/ta_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/ta_PP-OCRv5_mobile_rec) @ `1bb164dad1d8` | `inference.json` `9f845b15eef3272a…`<br>`inference.pdiparams` `7e7bb6c9cbcc401a…`<br>`inference.yml` `88a28f5a1bb30cab…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/ka_PP-OCRv3_mobile_rec` | paddleocr | recognition | kn | [PaddlePaddle/ka_PP-OCRv3_mobile_rec](https://huggingface.co/PaddlePaddle/ka_PP-OCRv3_mobile_rec) @ `fd08732bad5f` | `inference.json` `3be0576413bd881e…`<br>`inference.pdiparams` `5abb4f1c00e5fab3…`<br>`inference.yml` `948eb2cdc413b775…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/eng` | tesseract | recognition | en | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `eng.traineddata` `8280aed0782fe272…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/hin` | tesseract | recognition | hi | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `hin.traineddata` `bd2e65a2184af08a…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/mar` | tesseract | recognition | mr | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `mar.traineddata` `799405456528ffd6…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/tel` | tesseract | recognition | te | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `tel.traineddata` `81b0a1cfaf8bbf0f…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/tam` | tesseract | recognition | ta | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `tam.traineddata` `4b9ce85987f629dd…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/kan` | tesseract | recognition | kn | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `kan.traineddata` `6f195e0f33f0ec51…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-OCRv5_server_rec` | paddleocr | handwriting_recognition | en | [PaddlePaddle/PP-OCRv5_server_rec](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_rec) @ `b26c3587fda8` | `inference.json` `8e6e12e5d4253184…`<br>`inference.pdiparams` `63853f062a5f4089…`<br>`inference.yml` `2c719dba044c4e22…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `tesseract/osd` | tesseract | script_detection | * | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `osd.traineddata` `9cf5d576fcc47564…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-DocLayout_plus-L` | paddleocr | layout_detection | * | [PaddlePaddle/PP-DocLayout_plus-L](https://huggingface.co/PaddlePaddle/PP-DocLayout_plus-L) @ `aa52b8528c84` | `inference.json` `99bd09c0a1b9e88d…`<br>`inference.pdiparams` `24ca3e2e44216450…`<br>`inference.yml` `d60f782a16f96afb…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-DocLayout-M` | paddleocr | layout_detection | * | [PaddlePaddle/PP-DocLayout-M](https://huggingface.co/PaddlePaddle/PP-DocLayout-M) @ `7dbfcce3154a` | `inference.json` `5b2ec6d403904d40…`<br>`inference.pdiparams` `f374bb0269d91ab2…`<br>`inference.yml` `76aeb103310f432e…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/PP-LCNet_x1_0_table_cls` | paddleocr | table_classification | * | [PaddlePaddle/PP-LCNet_x1_0_table_cls](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_table_cls) @ `2fa6323e7dab` | `inference.json` `43401e76ed7b0e34…`<br>`inference.pdiparams` `d0224afd9cfddf22…`<br>`inference.yml` `891b1f4b0ccddaf6…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |
| `paddle/SLANet_plus` | paddleocr | table_structure | * | [PaddlePaddle/SLANet_plus](https://huggingface.co/PaddlePaddle/SLANet_plus) @ `bae6e5f8c3c4` | `inference.json` `aba91cd956f4a164…`<br>`inference.pdiparams` `012986b0e2bfe904…`<br>`inference.yml` `8a6372d3269a6f11…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor |

Total on disk: about 450 MB.

## Usage notes

- PaddleOCR uses `PP-OCRv5_mobile_det` by default; `PP-OCRv5_server_det` is pinned for evaluation.
- `PP-LCNet_x1_0_textline_ori` is used for English only; it garbles upright Indian-script lines
  (ADR-0021).
- `ka_PP-OCRv3_mobile_rec` is pinned but **disabled** (Kannada CER 0.388); Kannada uses Tesseract.
- `PP-OCRv5_server_rec` is the **experimental English handwriting** recogniser (ADR-0024). It is
  a Chinese/English model and drops English word spaces on some inputs (measured).
- `tesseract/osd` provides page orientation and (as a hint) script names (ADR-0023). Script
  identification itself uses a Tesseract census pass with the `eng/hin/tel/tam/kan` models.
- **Layout and tables (ADR-0025):** `PP-DocLayout_plus-L` is the default layout detector
  (20 region classes); `PP-DocLayout-M` (23 classes, about 5x faster, lower recall) is
  selectable with `NGOCR_LAYOUT_MODEL`. `PP-LCNet_x1_0_table_cls` classifies tables as wired /
  wireless; `SLANet_plus` predicts table structure (rows, columns, rowspan/colspan) and cell
  boxes. All four: Apache-2.0 weights; the vendor describes training data only as a
  "self-built dataset" of Chinese and English documents (not disclosed; risk accepted as for
  the OCR models). Not adopted: `SLANeXt_wired` (349 MB, about 20x slower, same structures on
  the probe) and the RT-DETR-L cell detectors (124 MB each).
- The Tesseract **binary** is the OS package `tesseract-ocr` 5.5.0 (Apache-2.0).

## Individual terms review (2026-10-24, Phase 11)

Every shipped model was re-checked at its pinned revision against its publisher's terms.
Only these models ship: the image build runs `fetch_models.py --verify`, which fails on any
file the lock does not list.

| Models | Terms checked | Finding |
|---|---|---|
| The 13 PaddlePaddle models above | The Hugging Face model card at the pinned revision: licence field and tag, gating, declared datasets, the card text | All Apache-2.0, none gated. No dataset declared, and no non-commercial or research-only wording. PP-DocLayout_plus-L and PP-DocLayout-M say they were "trained on a self-built dataset" of papers, slides, magazines, reports and similar documents |
| The 7 Tesseract models (`tessdata_best`) | `LICENSE` and `README.md` at the pinned commit | All data Apache-2.0. Training data is not described in the repository; the Tesseract documentation describes training on rendered text |

What this means:
- **Weights.** Apache-2.0 allows commercial use, modification and redistribution. It needs
  the licence and any NOTICE kept: THIRD_PARTY_NOTICES.md, and the image keeps the files
  as published.
- **Training data.** It is not disclosed for any model, so the data's own terms cannot be
  checked. This is accepted on the publishers' Apache-2.0 grants, and recorded as a known
  limitation (OD-3 covers handwriting models we might add). No model was trained by this
  project, and no production data is used for training.
- **Evaluation fonts.** The OFL fonts above are evaluation-only and are not in the image:
  `.dockerignore` excludes `evaluation/`.

## Evaluation-only assets (never shipped)

Handwriting-style fonts for the synthetic handwriting proxy (`make fonts`), SIL Open Font
License 1.1:

| Font | Scripts | Source @ revision | File (SHA-256) |
|---|---|---|---|
| Caveat | en | [google/fonts](https://github.com/google/fonts) @ `5e8a3ba89955` | `Caveat[wght].ttf` `0bdb6b660482d315…` |
| IndieFlower | en | [google/fonts](https://github.com/google/fonts) @ `5e8a3ba89955` | `IndieFlower-Regular.ttf` `ccc94b22b156e9c5…` |
| PatrickHand | en | [google/fonts](https://github.com/google/fonts) @ `5e8a3ba89955` | `PatrickHand-Regular.ttf` `0f173b3e6cb6d1af…` |
| ShadowsIntoLight | en | [google/fonts](https://github.com/google/fonts) @ `5e8a3ba89955` | `ShadowsIntoLight.ttf` `1347863151acdc00…` |
| Kalam | en, hi, mr | [google/fonts](https://github.com/google/fonts) @ `5e8a3ba89955` | `Kalam-Regular.ttf` `57cecb63d4608019…` |

## Handwriting models reviewed and rejected (2026-10-22)

| Candidate | Script | Reason not used |
|---|---|---|
| `CharanS247/got-ocr2-telugu-handwritten` | Telugu | Trained on IIIT-HW-Telugu (research-only terms), which conflicts with the claimed Apache-2.0; also a 580M-parameter VLM needing torch |
| `sabaridsnfuji/{Hindi,Tamil}_Offline_Handwritten_OCR` | hi, ta | Training data not disclosed; community upload |
| `UdayAgrawal29/handwritten-devanagari-text-recognition` | Devanagari | Training data not disclosed; very low usage |
| `microsoft/trocr-base-handwritten` | English | Weights MIT, but fine-tuned on IAM (non-commercial research licence); needs torch + transformers |
