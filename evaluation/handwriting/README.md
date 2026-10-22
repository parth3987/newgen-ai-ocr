# Real handwriting samples (not committed)

Handwriting support is **not claimed** for any language until it is measured on real samples
(docs/DECISIONS.md ADR-0024). Synthetic font renders are only a weak proxy.

## Providing samples

Put pairs of files in one directory **outside the repository** (they may contain personal data):

```
<lang>_<id>.jpg|png|tif   a photo or scan of one handwritten page or paragraph
<lang>_<id>.txt           its exact transcription, UTF-8, one line per handwritten line
```

`<lang>` is one of `en, hi, mr, te, ta, kn`, for example `en_001.jpg` + `en_001.txt`.

Requirements:
- Written by consenting people who agree to its use for internal evaluation. No real customer
  documents unless authorised.
- At least 30 pages per language from at least 5 different writers before any verdict.
- Transcriptions double-checked (ideally by a second person).

## Running

```bash
.venv/bin/python scripts/evaluate_handwriting.py --dataset /path/to/samples
```

English is measured with `paddle_handwriting`, `paddle` and `tesseract`; other languages with
`tesseract` only (no handwriting engine exists for them). Record results in
docs/ACCURACY_EVALUATION.md. Only after review may a language be added to
`NGOCR_OCR_HANDWRITING_VERIFIED_LANGUAGES` (which stops routing every handwriting page to
review) and the language matrix status changed.
