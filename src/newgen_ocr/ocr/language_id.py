"""Text-based script census and language identification (ADR-0023).

Recognised text is classified by Unicode script (which reveals mixed-script pages), and
Devanagari text is assigned to Hindi or Marathi by function-word evidence. When the
evidence is weak the result says so instead of guessing.
"""

from __future__ import annotations

import unicodedata

from newgen_ocr.ocr.models import LanguageGuess

_SCRIPT_PREFIXES = {
    "LATIN": "Latin",
    "DEVANAGARI": "Devanagari",
    "TELUGU": "Telugu",
    "TAMIL": "Tamil",
    "KANNADA": "Kannada",
}
_SCRIPT_LANGUAGE = {"Latin": "en", "Telugu": "te", "Tamil": "ta", "Kannada": "kn"}

# High-frequency words that differ between the two languages.
_HINDI = {
    "है",
    "हैं",
    "के",
    "की",
    "का",
    "में",
    "और",
    "से",
    "को",
    "पर",
    "यह",
    "लिए",
    "नहीं",
    "भी",
    "करें",
    "जाता",
}
_MARATHI = {
    "आहे",
    "आहेत",
    "आणि",
    "व",
    "हे",
    "या",
    "नाही",
    "करा",
    "साठी",
    "मध्ये",
    "केले",
    "होते",
    "असते",
    "ते",
    "आपल्या",
}
_MARATHI_SUFFIXES = ("च्या", "ाचे", "ाची", "ाचा", "मध्ये", "साठी", "ला", "ने")
# Minimum script share for a script to count as present on a page.
MIN_SCRIPT_SHARE = 0.05
MIN_DEVANAGARI_EVIDENCE = 3


def script_shares(text: str) -> dict[str, float]:
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha() and unicodedata.category(ch) not in ("Mn", "Mc"):
            continue
        name = unicodedata.name(ch, "")
        script = next((v for k, v in _SCRIPT_PREFIXES.items() if name.startswith(k)), "Other")
        counts[script] = counts.get(script, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def devanagari_language(text: str) -> tuple[str | None, int, int]:
    """("hi" | "mr" | None, hindi evidence, marathi evidence)."""
    hindi = marathi = 0
    for token in text.replace("।", " ").replace(".", " ").split():
        if token in _HINDI:
            hindi += 1
        if token in _MARATHI or (token.endswith(_MARATHI_SUFFIXES) and token not in _HINDI):
            marathi += 1
    total = hindi + marathi
    if total < MIN_DEVANAGARI_EVIDENCE or max(hindi, marathi) < 2 * min(hindi, marathi):
        return None, hindi, marathi
    return ("hi" if hindi > marathi else "mr"), hindi, marathi


def identify(text: str) -> LanguageGuess:
    shares = script_shares(text)
    present = [s for s, share in shares.items() if share >= MIN_SCRIPT_SHARE]
    if not present:
        return LanguageGuess(
            languages=[], scripts=shares, mixed_script=False, uncertain=True, reason="no_text"
        )
    languages: list[str] = []
    uncertain = False
    reason = "script"
    for script in present:
        if script == "Devanagari":
            language, hi, mr = devanagari_language(text)
            if language is None:
                languages += ["hi", "mr"]
                uncertain = True
                reason = f"devanagari_ambiguous(hi={hi},mr={mr})"
            else:
                languages.append(language)
                reason = f"devanagari_words(hi={hi},mr={mr})"
        elif script in _SCRIPT_LANGUAGE:
            languages.append(_SCRIPT_LANGUAGE[script])
        else:
            uncertain = True
            reason = "unsupported_script_in_text"
    return LanguageGuess(
        languages=languages,
        scripts=shares,
        mixed_script=len(present) > 1,
        uncertain=uncertain,
        reason=reason,
    )
