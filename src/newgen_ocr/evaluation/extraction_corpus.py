"""Synthetic extraction test set with exact ground truth (docs/ACCURACY_EVALUATION.md).

Documents are drawn like real ones (Noto fonts, ruled tables) and cover every built-in
schema, plus deliberately inconsistent and incomplete documents: wrong totals (checks must
fail), an unregistered buyer (the field must stay missing, never invented), an unbalanced
statement, and a letter that must not be classified. All identifiers are fake but
checksum-valid; identity documents are marked SPECIMEN. Ground truth per document: type,
every field's expected value (``None`` = absent from the page), table record counts and
expected check outcomes.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from newgen_ocr.evaluation.corpus import Gray, degrade_scan
from newgen_ocr.evaluation.layout_corpus import Canvas, CellSpec, TableSpec, draw_table

SELLER_MH = (
    "Northwind Supplies Pvt Ltd",
    "27AAACN1234A1Z7",
    "Plot 12, MIDC Andheri East, Mumbai 400093",
)
BUYER_MH = ("Sharma Traders", "27AABCS5678K1ZS", "45 FC Road, Pune 411004")
SELLER_KA = ("Kaveri Electricals Pvt Ltd", "29AAFCK4321M1ZA", "88 Residency Road, Bengaluru 560025")


@dataclass
class ExtractionSample:
    id: str
    document_type: str | None  # None: must stay unclassified
    language: str
    condition: str
    image: str
    fields: dict[str, str | None] = field(default_factory=dict)
    tables: dict[str, int] = field(default_factory=dict)  # table -> expected record count
    checks: dict[str, str] = field(default_factory=dict)  # check id -> expected status


def _grid(rows: list[list[str]]) -> tuple[CellSpec, ...]:
    return tuple(
        CellSpec(r, c, text, header=r == 0)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    )


def _lines(
    canvas: Canvas, x: float, y: float, lines: list[str], points: float = 10.5, language: str = "en"
) -> float:
    font = canvas.font(language, points)
    for line in lines:
        canvas.text(x, y, line, font)
        y += font.size * 1.55
    return y


def gst_invoice(variant: str, dpi: int) -> tuple[Gray, ExtractionSample]:
    """variant: intra | inter | error | unregistered."""
    c = Canvas(dpi)
    m = c.px(0.8)
    seller = SELLER_KA if variant == "inter" else SELLER_MH
    items = [
        ["Description", "HSN", "Qty", "Rate", "Amount"],
        ["Copier paper A4", "4802", "20", "250.00", "5000.00"],
        ["Toner cartridge", "8443", "2", "1800.00", "3600.00"],
        ["Box files", "4820", "10", "140.00", "1400.00"],
    ]
    if variant == "error":
        items[2][4] = "3800.00"  # 2 x 1800 is 3600: the line check must fail
    c.text(c.width / 2 - c.px(0.9), c.px(0.5), "TAX INVOICE", c.font("en", 16, bold=True))
    _lines(c, m, c.px(1.1), ["Invoice No: INV-2024-0042", "Invoice Date: 14/03/2024"])
    _lines(
        c,
        c.width / 2 + c.px(0.3),
        c.px(1.1),
        ["Due Date: 13/04/2024", "Place of Supply: Maharashtra"],
    )
    _lines(c, m, c.px(1.9), ["Seller:", seller[0], f"GSTIN: {seller[1]}", seller[2]])
    buyer_lines = ["Bill To:", BUYER_MH[0]]
    if variant != "unregistered":
        buyer_lines.append(f"GSTIN: {BUYER_MH[1]}")
    buyer_lines.append(BUYER_MH[2])
    _lines(c, c.width / 2 + c.px(0.3), c.px(1.9), buyer_lines)
    spec = TableSpec("items", "en", len(items), (2.4, 0.9, 0.7, 1.2, 1.3), _grid(items))
    draw_table(c, spec, m, c.px(3.6))
    y = c.px(3.6) + c.px(0.42) * len(items) + c.px(0.4)
    taxable = "10,000.00"
    if variant == "inter":
        totals = [f"Taxable Value: {taxable}", "IGST @ 18%: 1,800.00", "Grand Total: 11,800.00"]
    else:
        total = "11,900.00" if variant == "error" else "11,800.00"
        totals = [
            f"Taxable Value: {taxable}",
            "CGST @ 9%: 900.00",
            "SGST @ 9%: 900.00",
            f"Grand Total: {total}",
        ]
    _lines(c, c.width / 2 + c.px(0.6), y, totals)
    _lines(
        c,
        m,
        c.height - c.px(1.2),
        ["Thank you for your business. Subject to Mumbai jurisdiction."],
        9,
    )
    fields: dict[str, str | None] = {
        "invoice_number": "INV-2024-0042",
        "invoice_date": "2024-03-14",
        "due_date": "2024-04-13",
        "place_of_supply": "Maharashtra",
        "seller_name": seller[0],
        "seller_gstin": seller[1],
        "seller_address": seller[2],
        "buyer_name": BUYER_MH[0],
        "buyer_gstin": None if variant == "unregistered" else BUYER_MH[1],
        "buyer_address": BUYER_MH[2],
        "taxable_value": "10000.00",
        "cgst_amount": None if variant == "inter" else "900.00",
        "sgst_amount": None if variant == "inter" else "900.00",
        "igst_amount": "1800.00" if variant == "inter" else None,
        "round_off": None,
        "grand_total": "11900.00" if variant == "error" else "11800.00",
    }
    checks = {
        "line_amount": "failed" if variant == "error" else "passed",
        "items_total": "failed" if variant == "error" else "passed",
        "grand_total": "failed" if variant == "error" else "passed",
        "cgst_equals_sgst": "not_applicable" if variant == "inter" else "passed",
        "tax_type": "not_applicable" if variant == "unregistered" else "passed",
        "invoice_date_not_future": "passed",
        "due_after_invoice": "passed",
    }
    sample = ExtractionSample(
        id=f"gst-{variant}",
        document_type="gst_invoice",
        language="en",
        condition="",
        image="",
        fields=fields,
        tables={"line_items": 3},
        checks=checks,
    )
    return c.array(), sample


def gst_invoice_heldout(dpi: int) -> tuple[Gray, ExtractionSample]:
    """A second invoice design, written after the rules were tuned and never tuned on:
    different label wording, a written-out date, an Sl column and a ruled totals table."""
    c = Canvas(dpi)
    m = c.px(0.8)
    c.text(m, c.px(0.5), "Kaveri Electricals Pvt Ltd", c.font("en", 15, bold=True))
    _lines(c, m, c.px(0.9), ["88 Residency Road, Bengaluru 560025", "GSTIN/UIN: 29AAFCK4321M1ZA"])
    c.text(c.width - c.px(2.2), c.px(0.5), "INVOICE", c.font("en", 15, bold=True))
    _lines(c, m, c.px(1.7), ["Bill No. KE/24-25/118", "Dated: 2nd April 2024"])
    _lines(
        c,
        m,
        c.px(2.4),
        [
            "Buyer (Bill to)",
            "Lakshmi Hardware",
            "GSTIN/UIN: 29AABCL7788Q1ZC",
            "12 Market Street, Mysuru 570001",
        ],
    )
    items = [
        ["Sl", "Description of Goods", "HSN/SAC", "Quantity", "Rate", "Amount"],
        ["1", "LED bulb 9W", "8539", "30", "90.00", "2700.00"],
        ["2", "Switch board", "8536", "12", "150.00", "1800.00"],
    ]
    spec = TableSpec("items", "en", len(items), (0.5, 2.2, 1.0, 0.9, 1.0, 1.2), _grid(items))
    draw_table(c, spec, m, c.px(4.0))
    totals = [
        ["Taxable Value", "4,500.00"],
        ["CGST 6%", "270.00"],
        ["SGST 6%", "270.00"],
        ["Total Amount", "5,040.00"],
    ]
    tspec = TableSpec(
        "totals",
        "en",
        4,
        (2.0, 1.4),
        tuple(CellSpec(r, k, t) for r, row in enumerate(totals) for k, t in enumerate(row)),
    )
    draw_table(c, tspec, c.width - m - c.px(3.4), c.px(5.6))
    fields: dict[str, str | None] = {
        "invoice_number": "KE/24-25/118",
        "invoice_date": "2024-04-02",
        "due_date": None,
        "place_of_supply": None,
        "seller_name": "Kaveri Electricals Pvt Ltd",
        "seller_gstin": "29AAFCK4321M1ZA",
        "seller_address": "88 Residency Road, Bengaluru 560025",
        "buyer_name": "Lakshmi Hardware",
        "buyer_gstin": "29AABCL7788Q1ZC",
        "buyer_address": "12 Market Street, Mysuru 570001",
        "taxable_value": "4500.00",
        "cgst_amount": "270.00",
        "sgst_amount": "270.00",
        "igst_amount": None,
        "round_off": None,
        "grand_total": "5040.00",
    }
    checks = {
        "line_amount": "passed",
        "items_total": "passed",
        "grand_total": "passed",
        "cgst_equals_sgst": "passed",
        "tax_type": "passed",
    }
    return c.array(), ExtractionSample(
        id="gst-heldout",
        document_type="gst_invoice",
        language="en",
        condition="",
        image="",
        fields=fields,
        tables={"line_items": 2},
        checks=checks,
    )


def receipt(language: str, dpi: int) -> tuple[Gray, ExtractionSample]:
    c = Canvas(dpi)
    m = c.px(1.0)
    if language == "hi":
        heading, number, date_label, total_label = "रसीद", "Receipt No", "दिनांक", "कुल"
        items = [
            ["Item", "Qty", "Rate", "Amount"],
            ["Rice 5kg", "1", "450.00", "450.00"],
            ["Dal 1kg", "2", "200.00", "400.00"],
        ]
        total = "850.00"
    else:
        heading, number, date_label, total_label = "CASH RECEIPT", "Receipt No", "Date", "Total"
        items = [
            ["Item", "Qty", "Rate", "Amount"],
            ["Notebook", "5", "60.00", "300.00"],
            ["Pen set", "2", "175.00", "350.00"],
            ["Stapler", "1", "600.00", "600.00"],
        ]
        total = "1,250.00"
    c.text(m, c.px(0.6), "Ganesh General Stores", c.font("en", 15, bold=True))
    _lines(c, m, c.px(1.1), [heading], 13, language)
    # The receipt number is Latin text: drawn in the Latin font whatever the language.
    _lines(c, m, c.px(1.5), [f"{number}: R-1182"], 10.5, "en")
    _lines(c, m, c.px(1.5) + c.px(0.23), [f"{date_label}: 05/04/2024"], 10.5, language)
    spec = TableSpec("items", "en", len(items), (2.2, 0.8, 1.2, 1.3), _grid(items))
    draw_table(c, spec, m, c.px(2.4))
    y = c.px(2.4) + c.px(0.42) * len(items) + c.px(0.4)
    y = _lines(c, m + c.px(3.0), y, [f"{total_label}: {total}"], 11, language)
    _lines(c, m, y + c.px(0.2), ["Payment Mode: UPI", "Thank you, visit again"])
    fields: dict[str, str | None] = {
        "merchant_name": None,
        "receipt_number": "R-1182",
        "receipt_date": "2024-04-05",
        "merchant_gstin": None,
        "payment_mode": "UPI",
        "total": total.replace(",", ""),
    }
    sample = ExtractionSample(
        id=f"receipt-{language}",
        document_type="receipt",
        language=language,
        condition="",
        image="",
        fields=fields,
        tables={"items": len(items) - 1},
        checks={"line_amount": "passed", "items_total": "passed", "date_not_future": "passed"},
    )
    return c.array(), sample


def bank_statement(balanced: bool, dpi: int) -> tuple[Gray, ExtractionSample]:
    c = Canvas(dpi)
    m = c.px(0.8)
    rows = [
        ["Date", "Description", "Debit", "Credit", "Balance"],
        ["02/03/2024", "Salary credit", "", "45,000.00", "95,000.00"],
        ["05/03/2024", "Rent transfer", "18,000.00", "", "77,000.00"],
        ["12/03/2024", "Electricity bill", "2,350.00", "", "74,650.00"],
        ["20/03/2024", "UPI grocery", "1,400.00", "", "73,250.00"],
        ["28/03/2024", "Interest", "", "250.00", "73,500.00"],
    ]
    closing = "73,500.00" if balanced else "74,500.00"
    c.text(m, c.px(0.6), "STATEMENT OF ACCOUNT", c.font("en", 15, bold=True))
    _lines(
        c,
        m,
        c.px(1.1),
        [
            "Bank Name: Coastal Co-operative Bank",
            "Account Holder: Priya Raman",
            "Account No: 001234567890",
            "IFSC: CCBL0004521",
            "From: 01/03/2024 To: 31/03/2024",
            "Opening Balance: 50,000.00",
            f"Closing Balance: {closing}",
        ],
    )
    spec = TableSpec("txn", "en", len(rows), (1.3, 2.4, 1.2, 1.2, 1.3), _grid(rows))
    draw_table(c, spec, m, c.px(3.3))
    fields: dict[str, str | None] = {
        "bank_name": "Coastal Co-operative Bank",
        "account_holder": "Priya Raman",
        "account_number": "001234567890",
        "ifsc": "CCBL0004521",
        "period_start": "2024-03-01",
        "period_end": "2024-03-31",
        "opening_balance": "50000.00",
        "closing_balance": closing.replace(",", ""),
    }
    sample = ExtractionSample(
        id=f"bank-{'balanced' if balanced else 'mismatch'}",
        document_type="bank_statement",
        language="en",
        condition="",
        image="",
        fields=fields,
        tables={"transactions": 5},
        checks={
            "period_order": "passed",
            "balance_reconciles": "passed" if balanced else "failed",
            "running_balance": "passed",
            "dates_in_period": "passed",
        },
    )
    return c.array(), sample


def pan_card(dpi: int) -> tuple[Gray, ExtractionSample]:
    c = Canvas(dpi)
    m = c.px(1.0)
    c.text(m, c.px(0.6), "INCOME TAX DEPARTMENT        GOVT. OF INDIA", c.font("en", 12, bold=True))
    _lines(c, m, c.px(1.1), ["SPECIMEN - SYNTHETIC TEST DATA"], 9)
    _lines(
        c,
        m,
        c.px(1.5),
        [
            "Permanent Account Number Card",
            "ABCPK1234F",
            "Name",
            "RAVI KUMAR",
            "Father's Name",
            "SURESH KUMAR",
            "Date of Birth",
            "15/08/1990",
        ],
        11,
    )
    fields: dict[str, str | None] = {
        "pan": "ABCPK1234F",
        "name": "RAVI KUMAR",
        "father_name": "SURESH KUMAR",
        "date_of_birth": "1990-08-15",
    }
    return c.array(), ExtractionSample(
        id="pan-card",
        document_type="pan_card",
        language="en",
        condition="",
        image="",
        fields=fields,
        checks={"dob_not_future": "passed"},
    )


def aadhaar_card(dpi: int, valid: bool = True) -> tuple[Gray, ExtractionSample]:
    c = Canvas(dpi)
    m = c.px(1.0)
    number = "2468 1357 9026" if valid else "2468 1357 9027"
    c.text(m, c.px(0.6), "Government of India", c.font("en", 13, bold=True))
    _lines(
        c,
        m,
        c.px(1.0),
        ["SPECIMEN - SYNTHETIC TEST DATA", "Unique Identification Authority of India"],
        9,
    )
    _lines(
        c,
        m,
        c.px(1.6),
        [
            "Name: Anita Desai",
            "DOB: 02/11/1985",
            "Gender: Female",
            "Address: 14 Lake View Road,",
            "Kolkata, West Bengal 700029",
            f"Aadhaar No: {number}",
        ],
        11,
    )
    fields: dict[str, str | None] = {
        "aadhaar_number": "XXXX XXXX 9026" if valid else None,
        "name": "Anita Desai",
        "date_of_birth": "1985-11-02",
        "gender": "female",
        "address": "14 Lake View Road, Kolkata, West Bengal 700029",
        "pincode": None,
    }
    return c.array(), ExtractionSample(
        id=f"aadhaar-{'valid' if valid else 'bad-checksum'}",
        document_type="aadhaar_card",
        language="en",
        condition="",
        image="",
        fields=fields,
        checks={"dob_not_future": "passed"},
    )


def letter(dpi: int) -> tuple[Gray, ExtractionSample]:
    c = Canvas(dpi)
    _lines(
        c,
        c.px(1.0),
        c.px(1.0),
        [
            "Dear Customer,",
            "We are pleased to inform you that our office",
            "will remain closed on Monday for maintenance.",
            "Regards,",
            "The Management",
        ],
        11,
    )
    return c.array(), ExtractionSample(
        id="letter", document_type=None, language="en", condition="", image=""
    )


def build(out_dir: Path) -> list[ExtractionSample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[ExtractionSample] = []
    makers: list[Callable[[int], tuple[Gray, ExtractionSample]]] = [
        *(
            lambda dpi, v=v: gst_invoice(v, dpi)
            for v in ("intra", "inter", "error", "unregistered")
        ),
        lambda dpi: receipt("en", dpi),
        lambda dpi: receipt("hi", dpi),
        lambda dpi: bank_statement(True, dpi),
        lambda dpi: bank_statement(False, dpi),
        pan_card,
        aadhaar_card,
        lambda dpi: aadhaar_card(dpi, valid=False),
        letter,
        gst_invoice_heldout,
    ]
    for condition, dpi in (("clean", 300), ("scan", 200)):
        for make in makers:
            image, sample = make(dpi)
            if condition == "scan":
                image = degrade_scan(image, seed=len(samples))
            sample.id = f"{sample.id}-{condition}"
            sample.condition = condition
            sample.image = f"{sample.id}.png"
            buffer = io.BytesIO()
            Image.fromarray(image).save(buffer, "PNG", dpi=(dpi, dpi))
            (out_dir / sample.image).write_bytes(buffer.getvalue())
            samples.append(sample)
    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(s) for s in samples], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return samples


def load_manifest(corpus_dir: Path) -> list[ExtractionSample]:
    data = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    return [ExtractionSample(**item) for item in data]
