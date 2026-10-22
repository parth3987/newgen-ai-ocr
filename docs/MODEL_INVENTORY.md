# Model Inventory

Generated from `models.lock.json` (the source of truth) on 2026-10-22. Code, weights and
training-data terms are recorded **independently** for each model.

## Policy

- Models are fetched only at staging, by `make models` (`scripts/fetch_models.py`), from the
  exact revision pinned here. Every file's SHA-256 is recorded in `models.lock.json` and
  cross-checked against an independent publisher hash: the Hugging Face LFS SHA-256 for Paddle
  weights, and the GitHub git-blob SHA-1 for `tessdata_best`.
- At runtime the application only *reads* `models/`. Each model is hash-verified before first
  use (`MODEL_INTEGRITY_ERROR` on mismatch). A missing model is reported as
  `MODEL_NOT_AVAILABLE` and never downloaded (ADR-0020/0021).
- **Training data:** neither PaddlePaddle nor the Tesseract project discloses training-data
  sources or terms. The project owner accepted this risk on 2026-10-22 (ADR-0021). Revisit if
  a legal review requires otherwise.
- Vendor-reported accuracy is not relied on. Our own measurements are in
  ACCURACY_EVALUATION.md.

## Approved models

Code licence: PaddleOCR/PaddleX/PaddlePaddle and Tesseract are all Apache-2.0.

| Model | Engine | Kind | Languages | Source @ revision | Files (SHA-256) | Code licence | Weights licence | Training data |
|---|---|---|---|---|---|---|---|---|
| `paddle/PP-OCRv5_mobile_det` | paddleocr | detection | * | [PaddlePaddle/PP-OCRv5_mobile_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) @ `0d63e78e2b68` | `inference.json` `05feef1acb00aa4c…`<br>`inference.pdiparams` `afa1820cb16c1fd0…`<br>`inference.yml` `98069072e1b6b37d…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/PP-OCRv5_server_det` | paddleocr | detection | * | [PaddlePaddle/PP-OCRv5_server_det](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det) @ `ca867c897ecb` | `inference.json` `af5876933d8806a1…`<br>`inference.pdiparams` `183146fe9d991035…`<br>`inference.yml` `28fb721efc3634fc…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/PP-LCNet_x1_0_textline_ori` | paddleocr | textline_orientation | * | [PaddlePaddle/PP-LCNet_x1_0_textline_ori](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_textline_ori) @ `cd237a44b0e3` | `inference.json` `b81929eeaff8e52d…`<br>`inference.pdiparams` `0de2bcf996cf553e…`<br>`inference.yml` `8d5120d0e1a30a9d…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/en_PP-OCRv5_mobile_rec` | paddleocr | recognition | en | [PaddlePaddle/en_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/en_PP-OCRv5_mobile_rec) @ `267c36e24c33` | `inference.json` `fd1b6ec722ea841a…`<br>`inference.pdiparams` `3ec8a97ed6cefe85…`<br>`inference.yml` `27e91d0582f40168…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/devanagari_PP-OCRv5_mobile_rec` | paddleocr | recognition | hi, mr | [PaddlePaddle/devanagari_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/devanagari_PP-OCRv5_mobile_rec) @ `99dcce6d196b` | `inference.json` `20d3b121184ec6b7…`<br>`inference.pdiparams` `719be7d20bfe9530…`<br>`inference.yml` `9bd172dd26440c8c…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/te_PP-OCRv5_mobile_rec` | paddleocr | recognition | te | [PaddlePaddle/te_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/te_PP-OCRv5_mobile_rec) @ `151ab3b1c2f2` | `inference.json` `ad866e22872fbc0e…`<br>`inference.pdiparams` `45967d00d6b4af59…`<br>`inference.yml` `acebbe53f1831bf2…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/ta_PP-OCRv5_mobile_rec` | paddleocr | recognition | ta | [PaddlePaddle/ta_PP-OCRv5_mobile_rec](https://huggingface.co/PaddlePaddle/ta_PP-OCRv5_mobile_rec) @ `1bb164dad1d8` | `inference.json` `9f845b15eef3272a…`<br>`inference.pdiparams` `7e7bb6c9cbcc401a…`<br>`inference.yml` `88a28f5a1bb30cab…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `paddle/ka_PP-OCRv3_mobile_rec` | paddleocr | recognition | kn | [PaddlePaddle/ka_PP-OCRv3_mobile_rec](https://huggingface.co/PaddlePaddle/ka_PP-OCRv3_mobile_rec) @ `fd08732bad5f` | `inference.json` `3be0576413bd881e…`<br>`inference.pdiparams` `5abb4f1c00e5fab3…`<br>`inference.yml` `948eb2cdc413b775…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/eng` | tesseract | recognition | en | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `eng.traineddata` `8280aed0782fe272…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/hin` | tesseract | recognition | hi | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `hin.traineddata` `bd2e65a2184af08a…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/mar` | tesseract | recognition | mr | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `mar.traineddata` `799405456528ffd6…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/tel` | tesseract | recognition | te | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `tel.traineddata` `81b0a1cfaf8bbf0f…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/tam` | tesseract | recognition | ta | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `tam.traineddata` `4b9ce85987f629dd…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |
| `tesseract/kan` | tesseract | recognition | kn | [tesseract-ocr/tessdata_best](https://github.com/tesseract-ocr/tessdata_best) @ `e12c65a91594` | `kan.traineddata` `6f195e0f33f0ec51…` | Apache-2.0 | Apache-2.0 | Not disclosed by vendor (risk accepted) |

Total size on disk: about 198 MB.

## Usage notes

- PaddleOCR uses `PP-OCRv5_mobile_det` by default (`NGOCR_PADDLE_DETECTION_MODEL`).
  `PP-OCRv5_server_det` is pinned for evaluation but not the default.
- `PP-LCNet_x1_0_textline_ori` is applied only to English. It misclassifies upright Indic
  lines as upside down (measured on Devanagari and Tamil, ADR-0021).
- Kannada on PaddleOCR uses the older `ka_PP-OCRv3_mobile_rec` (no PP-OCRv5 Kannada model
  exists in PaddleOCR 3.7). See ACCURACY_EVALUATION.md for its measured accuracy.
- Tesseract uses `tessdata_best` (LSTM) via an explicit `--tessdata-dir`, never OS language
  packs. The Tesseract **binary** comes from the OS (`tesseract-ocr` 5.5.0, Apache-2.0).
