"""The supported-language matrix: the single source of truth for routing, the API and docs.

A language is enabled only with documented model provenance (MODEL_INVENTORY.md) and
measured results (ACCURACY_EVALUATION.md). Handwriting status is per language and is never
"supported" without real-sample evaluation (ADR-0023).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class HandwritingStatus(StrEnum):
    UNSUPPORTED = "unsupported"  # no engine; handwritten pages are routed to review
    EXPERIMENTAL = "experimental"  # engine available; results always routed to review
    SUPPORTED = "supported"  # verified on real handwriting samples (none yet)


class LanguageInfo(BaseModel):
    code: str
    name: str
    script: str  # Tesseract OSD script name
    printed_engines: list[str]  # engines measured for printed text, preferred first
    handwriting: HandwritingStatus
    notes: str


LANGUAGES: dict[str, LanguageInfo] = {
    info.code: info
    for info in (
        LanguageInfo(
            code="en",
            name="English",
            script="Latin",
            printed_engines=["paddle", "tesseract"],
            handwriting=HandwritingStatus.EXPERIMENTAL,
            notes="Handwriting via PP-OCRv5_server_rec; synthetic-font evaluation only.",
        ),
        LanguageInfo(
            code="hi",
            name="Hindi",
            script="Devanagari",
            printed_engines=["paddle", "tesseract"],
            handwriting=HandwritingStatus.UNSUPPORTED,
            notes="Shares the Devanagari recogniser with Marathi.",
        ),
        LanguageInfo(
            code="mr",
            name="Marathi",
            script="Devanagari",
            printed_engines=["paddle", "tesseract"],
            handwriting=HandwritingStatus.UNSUPPORTED,
            notes="Distinguished from Hindi by text-based identification.",
        ),
        LanguageInfo(
            code="te",
            name="Telugu",
            script="Telugu",
            printed_engines=["paddle", "tesseract"],
            handwriting=HandwritingStatus.UNSUPPORTED,
            notes="",
        ),
        LanguageInfo(
            code="ta",
            name="Tamil",
            script="Tamil",
            printed_engines=["paddle", "tesseract"],
            handwriting=HandwritingStatus.UNSUPPORTED,
            notes="",
        ),
        LanguageInfo(
            code="kn",
            name="Kannada",
            script="Kannada",
            printed_engines=["tesseract"],
            handwriting=HandwritingStatus.UNSUPPORTED,
            notes="PaddleOCR's Kannada model is not usable (CER 0.39); Tesseract only.",
        ),
    )
}

# Languages tried for a page whose dominant script was detected. Indic recognisers also read
# Latin, and Tesseract combines models, so English is added as a secondary language.
SCRIPT_LANGUAGES: dict[str, list[str]] = {
    "Latin": ["en"],
    "Devanagari": ["hi", "mr", "en"],
    "Telugu": ["te", "en"],
    "Tamil": ["ta", "en"],
    "Kannada": ["kn", "en"],
}
