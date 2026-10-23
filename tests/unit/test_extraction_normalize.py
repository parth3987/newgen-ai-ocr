"""Normalisation of dates, amounts, identifiers and text; Indian identifier checksums."""

from __future__ import annotations

import pytest

from newgen_ocr.extraction import normalize as n
from newgen_ocr.extraction.identifiers import (
    aadhaar_errors,
    gstin_check_char,
    gstin_errors,
    mask_aadhaar,
    verhoeff_digit,
    verhoeff_valid,
)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("14/03/2024", "2024-03-14"),
        ("14-03-24", "2024-03-14"),
        ("14.03.2024", "2024-03-14"),
        ("2024-03-14", "2024-03-14"),
        ("14 March 2024", "2024-03-14"),
        ("14-Mar-24", "2024-03-14"),
        ("2nd April 2024", "2024-04-02"),
        ("March 14, 2024", "2024-03-14"),
        ("14th Sept, 2024", "2024-09-14"),
        ("दिनांक: १४/०३/२०२४", "2024-03-14"),  # Devanagari digits
        ("తేదీ: ౧౪/౦౩/౨౦౨౪", "2024-03-14"),  # Telugu digits
    ],
)
def test_dates(text: str, value: str) -> None:
    result = n.normalize_date(text)
    assert result.ok and result.value == value


def test_date_policies_and_failures() -> None:
    assert n.normalize_date("03/04/2024").notes == ["numeric date read day-first"]
    month_first = n.normalize_date("03/04/2024", day_first=False)
    assert month_first.value is None
    assert month_first.ambiguous == ["2024-04-03", "2024-03-04"]
    assert n.normalize_date("13/04/2024", day_first=False).value == "2024-04-13"  # only one way
    assert n.normalize_date("31/02/2024").errors == ["not a valid calendar date"]
    several = n.normalize_date("From 01/03/2024 to 31/03/2024")
    assert several.value is None and len(several.ambiguous) == 2
    absent = n.normalize_date("no date here")
    assert absent.absent and absent.value is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("₹ 1,23,456.78", "123456.78"),
        ("Rs. 11,800.00", "11800.00"),
        ("INR 1,000", "1000.00"),
        ("11800", "11800.00"),
        ("-250.5", "-250.50"),
        ("GST 18% 1,800.00", "1800.00"),  # the rate is not an amount
        ("Total 500.", "500.00"),
        ("\u0967,\u0968\u096b\u0966.\u0966\u0966", "1250.00"),  # Devanagari digits
    ],
)
def test_amounts(text: str, value: str) -> None:
    result = n.normalize_amount(text)
    assert result.ok and result.value == value


def test_amount_failures() -> None:
    assert n.normalize_amount("1,2345.00").errors == ["amount digit grouping is not valid"]
    two = n.normalize_amount("Total 10 11800.00")
    assert two.value is None and two.ambiguous == ["10.00", "11800.00"]
    assert n.normalize_amount("12.5%").absent
    assert n.normalize_amount("500.00 Cr").notes == ["marked CR"]


def test_numbers_and_percent() -> None:
    assert n.normalize_number("12 pcs").value == "12"
    assert n.normalize_number("1,250.5 kg").value == "1250.5"
    assert n.normalize_number("none").absent
    assert n.normalize_percent("GST @ 18%").value == "18.00"
    assert n.normalize_percent("eighteen").absent


def test_gstin_checksum_and_state() -> None:
    assert gstin_check_char("27AAPFU0939F1Z") == "V"  # published GSTN example
    assert gstin_errors("27AAPFU0939F1ZV") == []
    assert gstin_errors("27AAPFU0939F1ZX") == ["GSTIN check character does not match"]
    assert gstin_errors("98AAPFU0939F1ZV")[0] == "unknown state code 98"
    assert gstin_errors("nonsense") == ["not in GSTIN format"]
    result = n.normalize_gstin("GSTIN: 27 AAPFU 0939F1ZV")
    assert result.ok and result.notes == ["state Maharashtra"]
    bad = n.normalize_gstin("27AAPFU0939F1ZX")
    assert bad.value == "27AAPFU0939F1ZX" and bad.errors and not bad.ok
    assert n.normalize_gstin("29AAFCK4321M1ZA 27AAPFU0939F1ZV").ambiguous


def test_verhoeff_and_aadhaar_masking() -> None:
    assert verhoeff_digit("236") == "3" and verhoeff_valid("2363")  # textbook example
    assert not verhoeff_valid("2364")
    assert aadhaar_errors("246813579026") == []
    assert aadhaar_errors("246813579027") == ["Aadhaar check digit does not match"]
    assert aadhaar_errors("123456789012")[0].startswith("not in Aadhaar format")
    assert mask_aadhaar("246813579026") == "XXXX XXXX 9026"
    masked = n.normalize_aadhaar("Aadhaar No: 2468 1357 9026")
    assert masked.ok and masked.value == "XXXX XXXX 9026"
    assert n.normalize_aadhaar("2468 1357 9026", mask=False).value == "246813579026"
    invalid = n.normalize_aadhaar("2468 1357 9027")
    assert invalid.value == "XXXX XXXX 9027" and invalid.errors


def test_pan_ifsc_identifier_text() -> None:
    assert n.normalize_pan("PAN ABCPK1234F").value == "ABCPK1234F"
    assert n.normalize_pan("Card").absent
    assert n.normalize_ifsc("IFSC: sbin0001234").value == "SBIN0001234"
    assert n.normalize_identifier("INV-2024-0042 dated").value == "INV-2024-0042"
    assert n.normalize_identifier("Bill No. KE/24-25/118", r"[A-Z]{2}/[\d-]+/\d+").value == (
        "KE/24-25/118"
    )
    assert n.normalize_identifier("—").absent
    assert n.normalize_identifier("x", r"\d{5}").absent
    assert n.normalize_text("  Sharma   Traders ,").value == "Sharma Traders"


def test_address_pincode_phone_gender() -> None:
    address = n.normalize_address("14 Lake View Road, , Kolkata,  700029,")
    assert address.value == "14 Lake View Road, Kolkata, 700029"
    assert address.notes == ["PIN 700029"]
    assert n.normalize_pincode("Pune 411 004").value == "411004"
    assert n.normalize_pincode("400001 and 411004").ambiguous
    assert n.normalize_phone("+91 98450 12345").value == "+919845012345"
    assert n.normalize_phone("12345").errors
    assert n.normalize_gender("Gender: Female").value == "female"
    assert n.normalize_gender("पुरुष / MALE").value == "male"
    assert n.normalize_gender("Male Female").ambiguous
    assert n.normalize_gender("n/a").errors
