"""Value normalisation: OCR text to typed values, or an explicit reason why not (ADR-0026).

Every function returns a ``Normalized``: a JSON-friendly value plus notes (assumptions made)
and errors (why the text is not a valid value). Nothing is guessed: text with two possible
readings is reported ``ambiguous`` with all readings, and text that does not parse is
``invalid`` with the raw text kept.

Conventions: dates are ISO 8601 strings; amounts are decimal strings with two places (no
binary floats); identifiers are upper-case without spaces; digits in Indian scripts
(Devanagari, Telugu, Tamil, Kannada, …) are converted to ASCII first.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from newgen_ocr.extraction.identifiers import (
    AADHAAR_RE,
    GST_STATES,
    GSTIN_RE,
    IFSC_RE,
    PAN_RE,
    aadhaar_errors,
    gstin_errors,
    ifsc_errors,
    mask_aadhaar,
    pan_errors,
)

JsonValue = str | int | bool | None


@dataclass
class Normalized:
    value: JsonValue = None
    ambiguous: list[str] = field(default_factory=list)  # alternative readings
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The part of the text the value was read from (for evidence; may be masked).
    matched: str | None = None
    # Nothing of the requested type occurs in the text at all (e.g. "Card" read as a PAN).
    absent: bool = False

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.errors and not self.ambiguous


def ascii_digits(text: str) -> str:
    """Replace digits of any script (e.g. ४, ౪, ௪, ೪) with ASCII digits."""
    return "".join(
        str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in text
    )


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", ascii_digits(text))
    return re.sub(r"\s+", " ", text).strip(" \t:;,|-\u2013\u2014")


# ------------------------------------------------------------ dates

_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}  # fmt: skip
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_PATTERNS = [
    # 2024-03-14
    ("iso", re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")),
    # 14/03/2024, 14-03-24, 14.03.2024
    ("numeric", re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b")),
    # 14 March 2024, 14-Mar-2024, 14th Mar, 2024
    (
        "dmy_text",
        re.compile(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.,]*({_MONTH})[a-z]*[\s\-/.,]*(\d{{4}}|\d{{2}})\b",
            re.I,
        ),
    ),
    # March 14, 2024
    (
        "mdy_text",
        re.compile(rf"\b({_MONTH})[a-z]*[\s.]+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
    ),
]


def _year(text: str) -> int:
    year = int(text)
    if len(text) == 2:
        return 2000 + year if year < 70 else 1900 + year
    return year


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def normalize_date(text: str, day_first: bool = True) -> Normalized:
    """Find one date in the text. Numeric dates are read day-first (Indian convention) when
    ``day_first``; otherwise a numeric date valid both ways is ambiguous."""
    text = ascii_digits(text)
    found: list[tuple[date, str, str]] = []
    invalid: list[str] = []
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            a, b, c = match.groups()
            readings: list[date | None]
            if kind == "iso":
                readings = [_make(int(a), int(b), int(c))]
            elif kind == "numeric":
                y = _year(c)
                dmy, mdy = _make(y, int(b), int(a)), _make(y, int(a), int(b))
                if day_first or mdy is None or dmy == mdy:
                    readings = [dmy] if dmy else [mdy]
                elif dmy is None:
                    readings = [mdy]
                else:
                    readings = [dmy, mdy]
            elif kind == "dmy_text":
                readings = [_make(_year(c), _MONTHS[b.lower()[:3]], int(a))]
            else:
                readings = [_make(int(c), _MONTHS[a.lower()[:3]], int(b))]
            valid = [r for r in readings if r is not None]
            if not valid:
                invalid.append(match.group(0))
            if len(valid) == 2:
                return Normalized(
                    ambiguous=[v.isoformat() for v in valid],
                    matched=match.group(0),
                    notes=["day/month order unknown"],
                )
            if valid:
                note = "numeric date read day-first" if kind == "numeric" and day_first else ""
                found.append((valid[0], match.group(0), note))
    distinct = {d for d, _, _ in found}
    if not found:
        if invalid:
            return Normalized(matched=invalid[0], errors=["not a valid calendar date"])
        return Normalized(errors=["no date found"], absent=True)
    if len(distinct) > 1:
        return Normalized(
            ambiguous=sorted(d.isoformat() for d in distinct), notes=["several dates in text"]
        )
    when, matched, note = found[0]
    return Normalized(value=when.isoformat(), matched=matched, notes=[note] if note else [])


# ------------------------------------------------------------ amounts

_AMOUNT = re.compile(
    r"(?<![\w.,])(?:₹|rs\.?|inr)?\s*([-\u2212]?\d[\d,]*(?:\.\d{1,2})?)(?:\s*(cr|dr))?(?!\.?\d|[\w,%]|\s*%)",
    re.I,
)
_TWO_PLACES = Decimal("0.01")


def to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").replace("\u2212", "-").replace(" ", ""))
    except InvalidOperation:
        return None


def format_amount(value: Decimal) -> str:
    return str(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _grouping_ok(number: str) -> bool:
    """Indian (1,23,456) or international (123,456) digit grouping, or none."""
    integer = number.split(".")[0].lstrip("-\u2212").strip()
    if "," not in integer:
        return True
    groups = integer.split(",")
    if any(not g.isdigit() for g in groups) or len(groups[-1]) != 3:
        return False
    middle = groups[1:-1]
    return all(len(g) == 3 for g in middle) or all(len(g) == 2 for g in middle)


def normalize_amount(text: str) -> Normalized:
    """One monetary amount. ``Cr``/``Dr`` suffixes are kept as notes (sign is not inferred)."""
    text = ascii_digits(text)
    matches = [m for m in _AMOUNT.finditer(text) if any(ch.isdigit() for ch in m.group(1))]
    if not matches:
        return Normalized(errors=["no amount found"], absent=True)
    values = []
    for match in matches:
        number = match.group(1)
        if not _grouping_ok(number):
            continue
        value = to_decimal(number)
        if value is not None:
            values.append((value, match))
    distinct = {v for v, _ in values}
    if not distinct:
        return Normalized(errors=["amount digit grouping is not valid"])
    if len(distinct) > 1:
        return Normalized(
            ambiguous=[format_amount(v) for v in sorted(distinct)],
            notes=["several amounts in text"],
        )
    value, match = values[0]
    notes = [f"marked {match.group(2).upper()}"] if match.group(2) else []
    return Normalized(value=format_amount(value), matched=match.group(0).strip(), notes=notes)


def normalize_percent(text: str) -> Normalized:
    match = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*%", ascii_digits(text))
    if not match:
        return Normalized(errors=["no percentage found"], absent=True)
    return Normalized(value=format_amount(Decimal(match.group(1))), matched=match.group(0))


def normalize_number(text: str) -> Normalized:
    """A quantity: integer or decimal, grouping allowed, unit words ignored."""
    match = re.search(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)", ascii_digits(text))
    if not match or not _grouping_ok(match.group(1)):
        return Normalized(errors=["no number found"], absent=True)
    value = to_decimal(match.group(1))
    if value is None:
        return Normalized(errors=["no number found"], absent=True)
    out = str(value.normalize()) if value == value.to_integral() else str(value)
    if "E" in out:
        out = str(value.quantize(Decimal(1)))
    return Normalized(value=out, matched=match.group(0))


# ------------------------------------------------------------ identifiers


def _identifier(text: str, pattern: re.Pattern[str], check: object, kind: str) -> Normalized:
    upper = ascii_digits(text).upper()
    # Spaced text first (keeps neighbouring words apart); then with spaces removed, for
    # identifiers OCR split into pieces ("27 AAPFU 0939F1ZV").
    found = sorted(set(pattern.findall(upper))) or sorted(
        set(pattern.findall(re.sub(r"\s+", "", upper)))
    )
    if not found:
        return Normalized(errors=[f"no {kind} found"], absent=True)
    if len(found) > 1:
        return Normalized(ambiguous=list(found), notes=[f"several {kind}s in text"])
    value = found[0]
    errors = check(value)  # type: ignore[operator]
    return Normalized(value=value, matched=value, errors=list(errors))


def normalize_gstin(text: str) -> Normalized:
    result = _identifier(text, GSTIN_RE, gstin_errors, "GSTIN")
    if result.value and not result.errors:
        result.notes.append(f"state {GST_STATES[str(result.value)[:2]]}")
    return result


def normalize_pan(text: str) -> Normalized:
    return _identifier(text, PAN_RE, pan_errors, "PAN")


def normalize_ifsc(text: str) -> Normalized:
    return _identifier(text, IFSC_RE, ifsc_errors, "IFSC")


def normalize_aadhaar(text: str, mask: bool = True) -> Normalized:
    matches = AADHAAR_RE.findall(ascii_digits(text))
    digits = sorted({re.sub(r"\D", "", m) for m in matches})
    if not digits:
        return Normalized(errors=["no Aadhaar number found"], absent=True)
    shown = [mask_aadhaar(d) if mask else d for d in digits]
    if len(digits) > 1:
        return Normalized(ambiguous=list(shown), notes=["several Aadhaar numbers in text"])
    notes = ["masked to the last 4 digits"] if mask else []
    return Normalized(
        value=shown[0], matched=shown[0], errors=aadhaar_errors(digits[0]), notes=notes
    )


def normalize_identifier(text: str, pattern: str | None = None) -> Normalized:
    """A free-form reference (invoice number, account number): first token, or ``pattern``."""
    text = clean(ascii_digits(text))
    if pattern:
        found = sorted(set(re.findall(pattern, text)))
        if not found:
            return Normalized(errors=["no value matches the field pattern"], absent=True)
        if len(found) > 1:
            return Normalized(ambiguous=list(found), notes=["several matches in text"])
        return Normalized(value=found[0], matched=found[0])
    token = text.split(" ")[0] if text else ""
    if not token or not any(ch.isalnum() for ch in token):
        return Normalized(errors=["empty value"], absent=True)
    return Normalized(value=token, matched=token)


def normalize_text(text: str) -> Normalized:
    value = clean(text)
    return (
        Normalized(value=value, matched=value)
        if value
        else Normalized(errors=["empty value"], absent=True)
    )


def normalize_pincode(text: str) -> Normalized:
    found = sorted(set(re.findall(r"(?<!\d)([1-9]\d{2}\s?\d{3})(?!\d)", ascii_digits(text))))
    found = sorted({f.replace(" ", "") for f in found})
    if not found:
        return Normalized(errors=["no PIN code found"], absent=True)
    if len(found) > 1:
        return Normalized(ambiguous=list(found))
    return Normalized(value=found[0], matched=found[0])


def normalize_phone(text: str) -> Normalized:
    digits = re.sub(r"\D", "", ascii_digits(text))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if re.fullmatch(r"[6-9]\d{9}", digits):
        return Normalized(value=f"+91{digits}", matched=digits)
    return Normalized(errors=["not an Indian mobile number"])


def normalize_address(text: str) -> Normalized:
    """Whitespace-normalised address; the PIN code is noted when exactly one is present."""
    value = re.sub(r"(\s*,)+\s*", ", ", clean(text)).strip(", ")
    if not value:
        return Normalized(errors=["empty value"], absent=True)
    pin = normalize_pincode(value)
    notes = [f"PIN {pin.value}"] if pin.ok else []
    return Normalized(value=value, matched=value, notes=notes)


def normalize_gender(text: str) -> Normalized:
    words = clean(text).lower()
    table = {
        "male": "male", "female": "female", "पुरुष": "male", "महिला": "female",
        "transgender": "transgender",
    }  # fmt: skip
    found = {v for k, v in table.items() if re.search(rf"(?<!\w){k}(?!\w)", words)}
    if len(found) == 1:
        return Normalized(value=found.pop(), matched=words)
    if found:
        return Normalized(ambiguous=sorted(found))
    return Normalized(errors=["no gender found"], absent=True)


NORMALIZERS = {
    "date": normalize_date,
    "amount": normalize_amount,
    "percent": normalize_percent,
    "number": normalize_number,
    "gstin": normalize_gstin,
    "pan": normalize_pan,
    "ifsc": normalize_ifsc,
    "aadhaar": normalize_aadhaar,
    "identifier": normalize_identifier,
    "text": normalize_text,
    "name": normalize_text,
    "address": normalize_address,
    "pincode": normalize_pincode,
    "phone": normalize_phone,
    "gender": normalize_gender,
}
