"""Indian identifier formats and checksums (GSTIN, PAN, Aadhaar, IFSC).

Only published, deterministic rules are applied; nothing is looked up online.
- GSTIN: 2-digit state code + PAN + entity number + 'Z' + check character (mod-36 checksum
  over the first 14 characters, as published by GSTN).
- PAN: 5 letters, 4 digits, 1 letter; the 4th letter is the holder type (P person, C company…).
- Aadhaar: 12 digits, not starting with 0 or 1, last digit a Verhoeff check digit. Aadhaar
  numbers are masked to the last 4 digits by default (ADR-0026).
- IFSC: 4 letters, '0', 6 alphanumerics.
"""

from __future__ import annotations

import re

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
PAN_RE = re.compile(r"\b([A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z])\b")
AADHAAR_RE = re.compile(r"(?<!\d)([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)")
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# GST state codes (first two GSTIN digits). 97: other territory, 99: centre jurisdiction.
GST_STATES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}  # fmt: skip

PAN_HOLDER_TYPES = {
    "A": "association of persons", "B": "body of individuals", "C": "company",
    "F": "firm", "G": "government", "H": "hindu undivided family",
    "J": "artificial juridical person",
    "L": "local authority", "P": "individual", "T": "trust",
}  # fmt: skip


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        value = _GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def gstin_errors(value: str) -> list[str]:
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value):
        return ["not in GSTIN format"]
    errors = []
    if value[:2] not in GST_STATES:
        errors.append(f"unknown state code {value[:2]}")
    if gstin_check_char(value[:14]) != value[14]:
        errors.append("GSTIN check character does not match")
    return errors


def pan_errors(value: str) -> list[str]:
    return [] if PAN_RE.fullmatch(value) else ["not in PAN format"]


# Verhoeff tables (dihedral group D5).
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]  # fmt: skip
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]  # fmt: skip
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[i % 8][int(ch)]]
    return check == 0


def verhoeff_digit(digits: str) -> str:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[check])


def aadhaar_errors(digits: str) -> list[str]:
    if not re.fullmatch(r"[2-9]\d{11}", digits):
        return ["not in Aadhaar format (12 digits, not starting with 0 or 1)"]
    return [] if verhoeff_valid(digits) else ["Aadhaar check digit does not match"]


def mask_aadhaar(digits: str) -> str:
    return f"XXXX XXXX {digits[-4:]}"


def ifsc_errors(value: str) -> list[str]:
    return [] if IFSC_RE.fullmatch(value) else ["not in IFSC format"]
