"""Seeded, varied synthetic documents for calibrating review thresholds (ADR-0028).

Separate from the extraction test set the rules were tuned on: every document draws its
values, label wording, layout, date format and scan condition from a seeded generator, and
the set is split into a **calibration** part (thresholds are fitted on it) and a **test**
part (thresholds are only measured on it). Conditions include heavily degraded low-DPI scans
so that OCR errors, and therefore wrong values, actually occur.

This is synthetic data: it shows that the method works and how confidence relates to
correctness on these documents. Thresholds for production use must be fitted on real,
labelled documents (``scripts/calibrate_review.py --dataset``; OD-28).
"""

from __future__ import annotations

import io
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from newgen_ocr.evaluation.corpus import Gray, degrade_scan
from newgen_ocr.evaluation.extraction_corpus import ExtractionSample, _grid, _lines
from newgen_ocr.evaluation.layout_corpus import Canvas, TableSpec, draw_table
from newgen_ocr.extraction.identifiers import gstin_check_char, verhoeff_digit
from newgen_ocr.extraction.normalize import format_amount

TYPES = ("gst_invoice", "receipt", "bank_statement", "pan_card", "aadhaar_card")
CONDITIONS = (("clean", 300, 0.3), ("scan", 200, 0.4), ("heavy", 150, 0.3))

COMPANIES = [
    "Northwind Supplies Pvt Ltd", "Kaveri Electricals Pvt Ltd", "Sahyadri Agro Foods LLP",
    "Ganga Textiles Ltd", "Deccan Office Systems", "Malabar Spices Co", "Indus Pharma Pvt Ltd",
    "Konark Steel Traders", "Vindhya Paper Mills", "Coromandel Plastics", "Narmada Hardware",
    "Shivalik Packaging", "Godavari Chemicals Ltd", "Aravali Logistics", "Sunrise Stationers",
]  # fmt: skip
SHOPS = [
    "Ganesh General Stores", "Lakshmi Provisions", "Annapurna Sweets", "City Medicals",
    "Krishna Book Depot", "Balaji Kirana", "New India Bakery", "Sai Hardware Mart",
]  # fmt: skip
PEOPLE = [
    ("RAVI KUMAR", "SURESH KUMAR"), ("ANITA DESAI", "MOHAN DESAI"), ("PRIYA RAMAN", "K RAMAN"),
    ("ARJUN NAIR", "VIJAY NAIR"), ("MEENA IYER", "S IYER"), ("FARHAN SHAIKH", "IQBAL SHAIKH"),
    ("KAVYA REDDY", "SRINIVAS REDDY"), ("ROHAN MEHTA", "ASHOK MEHTA"),
    ("DIVYA PATIL", "SANJAY PATIL"), ("SUNIL JOSHI", "PRAKASH JOSHI"),
]  # fmt: skip
PLACES = [
    ("27", "Maharashtra", "Mumbai", "400093", "Plot 12, MIDC Andheri East"),
    ("27", "Maharashtra", "Pune", "411004", "45 FC Road"),
    ("29", "Karnataka", "Bengaluru", "560025", "88 Residency Road"),
    ("29", "Karnataka", "Mysuru", "570001", "12 Market Street"),
    ("33", "Tamil Nadu", "Chennai", "600002", "21 Anna Salai"),
    ("36", "Telangana", "Hyderabad", "500081", "7 Hitech City Road"),
    ("07", "Delhi", "New Delhi", "110001", "14 Connaught Place"),
    ("24", "Gujarat", "Ahmedabad", "380009", "3 CG Road"),
]  # fmt: skip
BANKS = ["Coastal Co-operative Bank", "Union Federal Bank", "Sahyadri Bank", "Metro City Bank"]
ITEMS = [
    ("Copier paper A4", "4802"), ("Toner cartridge", "8443"), ("Box files", "4820"),
    ("LED bulb 9W", "8539"), ("Switch board", "8536"), ("Steel almirah", "9403"),
    ("Office chair", "9401"), ("Printer cable", "8544"), ("Notebook", "4820"),
    ("Hand sanitiser", "3808"),
]  # fmt: skip
RECEIPT_ITEMS = ["Rice 5kg", "Dal 1kg", "Sugar 1kg", "Tea 250g", "Soap", "Notebook", "Pen set"]
NARRATIONS = ["Salary credit", "Rent transfer", "Electricity bill", "UPI grocery", "ATM cash",
              "Interest", "Insurance premium", "NEFT from client", "Mobile recharge"]  # fmt: skip
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class CalibrationSample(ExtractionSample):
    split: str = "calibration"  # "calibration" or "test"
    seed: int = 0


def _money(value: Decimal) -> str:
    """Indian digit grouping, two decimals (12,34,567.50)."""
    whole, frac = format_amount(value).split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail]) + "." + frac if groups else f"{tail}.{frac}"


def _pan(rng: random.Random, holder: str) -> str:
    return (
        "".join(rng.choice(LETTERS) for _ in range(3))
        + holder
        + rng.choice(LETTERS)
        + f"{rng.randrange(10000):04d}"
        + rng.choice(LETTERS)
    )


def _gstin(rng: random.Random, state: str) -> str:
    first14 = state + _pan(rng, "C") + str(rng.randrange(1, 10)) + "Z"
    return first14 + gstin_check_char(first14)


def _aadhaar(rng: random.Random) -> str:
    first = str(rng.randrange(2, 10)) + "".join(str(rng.randrange(10)) for _ in range(10))
    return first + verhoeff_digit(first)


def _day(rng: random.Random) -> date:
    return date(2023, 1, 1) + timedelta(days=rng.randrange(700))


def _date_text(rng: random.Random, day: date) -> str:
    return rng.choice(
        [
            day.strftime("%d/%m/%Y"),
            day.strftime("%d-%m-%Y"),
            day.strftime("%d.%m.%Y"),
            day.strftime("%d %b %Y"),
            day.strftime("%d-%b-%Y"),
            day.strftime("%d %B %Y"),
        ]
    )


def degrade_heavy(page: Gray, seed: int) -> Gray:
    """A poor scan: more skew, blur and noise, strong JPEG compression."""
    rng = np.random.default_rng(seed)
    h, w = page.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), float(rng.uniform(-2.0, 2.0)), 1.0)
    out = cv2.warpAffine(page, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    out = cv2.GaussianBlur(out, (0, 0), float(rng.uniform(0.8, 1.2)))
    out = np.clip(out + rng.normal(0, 14, out.shape), 0, 255).astype(np.uint8)
    _, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 45])
    return np.asarray(cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE), dtype=np.uint8)


def gst_invoice(rng: random.Random, dpi: int) -> tuple[Gray, dict[str, str | None], int]:
    c = Canvas(dpi)
    m = c.px(0.8)
    seller_place = rng.choice(PLACES)
    inter = rng.random() < 0.3
    buyer_place = rng.choice([p for p in PLACES if (p[0] != seller_place[0]) == inter])
    seller, buyer = rng.sample(COMPANIES, 2)
    registered = rng.random() < 0.85
    seller_gstin, buyer_gstin = _gstin(rng, seller_place[0]), _gstin(rng, buyer_place[0])
    number = rng.choice(
        [
            f"INV-{rng.randrange(2023, 2026)}-{rng.randrange(1, 9999):04d}",
            f"TI/{rng.randrange(100, 9999)}",
            f"{rng.choice(['KE', 'NW', 'GT'])}/24-25/{rng.randrange(1, 999)}",
        ]
    )
    day = _day(rng)
    due = day + timedelta(days=rng.choice([15, 30, 45])) if rng.random() < 0.6 else None
    supply = buyer_place[1] if rng.random() < 0.6 else None
    rate = rng.choice([5, 12, 18])
    rows = [["Description", "HSN", "Qty", "Rate", "Amount"]]
    taxable = Decimal(0)
    for name, hsn in rng.sample(ITEMS, rng.randrange(2, 6)):
        qty = rng.randrange(1, 40)
        price = Decimal(rng.randrange(20, 4000) * 5) / 2
        amount = price * qty
        taxable += amount
        rows.append([name, hsn, str(qty), _money(price), _money(amount)])
    half = (taxable * rate / 200).quantize(Decimal("0.01"))
    igst = (taxable * rate / 100).quantize(Decimal("0.01"))
    total = taxable + (igst if inter else 2 * half)

    c.text(c.width / 2 - c.px(0.9), c.px(0.5), "TAX INVOICE", c.font("en", 16, bold=True))
    meta = [
        f"{rng.choice(['Invoice No', 'Invoice Number', 'Inv No', 'Bill No'])}: {number}",
        f"{rng.choice(['Invoice Date', 'Date', 'Dated'])}: {_date_text(rng, day)}",
    ]
    right = []
    if due:
        right.append(f"Due Date: {_date_text(rng, due)}")
    if supply:
        right.append(f"Place of Supply: {supply}")
    y0 = c.px(1.1) + c.px(rng.uniform(0, 0.2))
    _lines(c, m, y0, meta)
    _lines(c, c.width / 2 + c.px(0.3), y0, right)
    seller_lines = [
        rng.choice(["Seller:", "Supplier:", "Sold By:"]),
        seller,
        f"GSTIN: {seller_gstin}",
        f"{seller_place[4]}, {seller_place[2]} {seller_place[3]}",
    ]
    buyer_lines = [rng.choice(["Bill To:", "Buyer:", "Billed To:"]), buyer]
    if registered:
        buyer_lines.append(f"GSTIN: {buyer_gstin}")
    buyer_lines.append(f"{buyer_place[4]}, {buyer_place[2]} {buyer_place[3]}")
    py = y0 + c.px(0.8)
    _lines(c, m, py, seller_lines)
    _lines(c, c.width / 2 + c.px(0.3), py, buyer_lines)
    ty = py + c.px(1.6)
    spec = TableSpec("items", "en", len(rows), (2.4, 0.9, 0.7, 1.2, 1.3), _grid(rows))
    draw_table(c, spec, m, ty)
    y = ty + c.px(0.42) * len(rows) + c.px(0.4)
    totals = [f"Taxable Value: {_money(taxable)}"]
    if inter:
        totals.append(f"IGST @ {rate}%: {_money(igst)}")
    else:
        totals += [f"CGST @ {rate / 2:g}%: {_money(half)}", f"SGST @ {rate / 2:g}%: {_money(half)}"]
    totals.append(
        f"{rng.choice(['Grand Total', 'Invoice Total', 'Total Amount'])}: {_money(total)}"
    )
    _lines(c, c.width / 2 + c.px(0.6), y, totals)
    _lines(c, m, c.height - c.px(1.2), ["Thank you for your business."], 9)
    fields: dict[str, str | None] = {
        "invoice_number": number,
        "invoice_date": day.isoformat(),
        "due_date": due.isoformat() if due else None,
        "place_of_supply": supply,
        "seller_name": seller,
        "seller_gstin": seller_gstin,
        "seller_address": f"{seller_place[4]}, {seller_place[2]} {seller_place[3]}",
        "buyer_name": buyer,
        "buyer_gstin": buyer_gstin if registered else None,
        "buyer_address": f"{buyer_place[4]}, {buyer_place[2]} {buyer_place[3]}",
        "taxable_value": format_amount(taxable),
        "cgst_amount": None if inter else format_amount(half),
        "sgst_amount": None if inter else format_amount(half),
        "igst_amount": format_amount(igst) if inter else None,
        "round_off": None,
        "grand_total": format_amount(total),
    }
    return c.array(), fields, len(rows) - 1


def receipt(rng: random.Random, dpi: int, language: str) -> tuple[Gray, dict[str, str | None], int]:
    c = Canvas(dpi)
    m = c.px(1.0)
    shop = rng.choice(SHOPS)
    rows = [["Item", "Qty", "Rate", "Amount"]]
    total = Decimal(0)
    for name in rng.sample(RECEIPT_ITEMS, rng.randrange(2, 5)):
        qty, price = rng.randrange(1, 6), Decimal(rng.randrange(10, 400) * 5)
        total += qty * price
        rows.append([name, str(qty), _money(price), _money(qty * price)])
    number = f"R-{rng.randrange(100, 99999)}"
    day = _day(rng)
    labelled_shop = rng.random() < 0.5
    gstin = _gstin(rng, rng.choice(PLACES)[0]) if rng.random() < 0.4 else None
    mode = rng.choice(["UPI", "Cash", "Card"]) if rng.random() < 0.7 else None
    heading, date_label, total_label = (
        ("रसीद", "दिनांक", "कुल") if language == "hi" else ("CASH RECEIPT", "Date", "Total")
    )
    c.text(m, c.px(0.6), shop, c.font("en", 15, bold=True))
    _lines(c, m, c.px(1.1), [heading], 13, language)
    head = [f"Receipt No: {number}"]
    if labelled_shop:
        head.append(f"Store: {shop}")
    if gstin:
        head.append(f"GSTIN: {gstin}")
    y = _lines(c, m, c.px(1.5), head, 10.5, "en")
    y = _lines(
        c,
        m,
        y,
        [f"{date_label}: {_date_text(rng, day) if language == 'en' else day.strftime('%d/%m/%Y')}"],
        10.5,
        language,
    )
    spec = TableSpec("items", "en", len(rows), (2.2, 0.8, 1.2, 1.3), _grid(rows))
    draw_table(c, spec, m, y + c.px(0.3))
    y += c.px(0.3) + c.px(0.42) * len(rows) + c.px(0.4)
    y = _lines(c, m + c.px(3.0), y, [f"{total_label}: {_money(total)}"], 11, language)
    tail = [f"Payment Mode: {mode}"] if mode else []
    _lines(c, m, y + c.px(0.2), [*tail, "Thank you, visit again"])
    fields: dict[str, str | None] = {
        "merchant_name": shop if labelled_shop else None,
        "receipt_number": number,
        "receipt_date": day.isoformat(),
        "merchant_gstin": gstin,
        "payment_mode": mode,
        "total": format_amount(total),
    }
    return c.array(), fields, len(rows) - 1


def bank_statement(rng: random.Random, dpi: int) -> tuple[Gray, dict[str, str | None], int]:
    c = Canvas(dpi)
    m = c.px(0.8)
    start = date(rng.randrange(2023, 2025), rng.randrange(1, 13), 1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    balance = Decimal(rng.randrange(1000, 200000) * 5)
    opening = balance
    reference = rng.random() < 0.5
    header = [
        "Date",
        "Description",
        *(["Ref No"] if reference else []),
        "Debit",
        "Credit",
        "Balance",
    ]
    rows = [header]
    days = sorted(rng.sample(range(1, end.day + 1), rng.randrange(3, 8)))
    for d in days:
        amount = Decimal(rng.randrange(100, 60000) * 5) / 2
        credit = rng.random() < 0.4 or amount > balance
        balance += amount if credit else -amount
        row = [start.replace(day=d).strftime("%d/%m/%Y"), rng.choice(NARRATIONS)]
        if reference:
            row.append(str(rng.randrange(100000, 999999)))
        row += ["", _money(amount)] if credit else [_money(amount), ""]
        row.append(_money(balance))
        rows.append(row)
    bank, holder = rng.choice(BANKS), rng.choice(PEOPLE)[0].title()
    account = "".join(str(rng.randrange(10)) for _ in range(rng.choice([11, 12, 14])))
    ifsc = "".join(rng.choice(LETTERS) for _ in range(4)) + "0" + f"{rng.randrange(10**6):06d}"
    c.text(
        m,
        c.px(0.6),
        rng.choice(["STATEMENT OF ACCOUNT", "ACCOUNT STATEMENT"]),
        c.font("en", 15, bold=True),
    )
    _lines(
        c,
        m,
        c.px(1.1),
        [
            f"Bank Name: {bank}",
            f"{rng.choice(['Account Holder', 'Account Name'])}: {holder}",
            f"{rng.choice(['Account No', 'A/C No'])}: {account}",
            f"IFSC: {ifsc}",
            f"From: {start.strftime('%d/%m/%Y')} To: {end.strftime('%d/%m/%Y')}",
            f"Opening Balance: {_money(opening)}",
            f"Closing Balance: {_money(balance)}",
        ],
    )
    widths = (1.3, 2.2, *((1.0,) if reference else ()), 1.1, 1.1, 1.3)
    spec = TableSpec("txn", "en", len(rows), widths, _grid(rows))
    draw_table(c, spec, m, c.px(3.3))
    fields: dict[str, str | None] = {
        "bank_name": bank,
        "account_holder": holder,
        "account_number": account,
        "ifsc": ifsc,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "opening_balance": format_amount(opening),
        "closing_balance": format_amount(balance),
    }
    return c.array(), fields, len(rows) - 1


def pan_card(rng: random.Random, dpi: int) -> tuple[Gray, dict[str, str | None], int]:
    c = Canvas(dpi)
    m = c.px(1.0) + c.px(rng.uniform(0, 0.5))
    name, father = rng.choice(PEOPLE)
    pan = _pan(rng, "P")
    dob = date(1950, 1, 1) + timedelta(days=rng.randrange(20000))
    c.text(m, c.px(0.6), "INCOME TAX DEPARTMENT        GOVT. OF INDIA", c.font("en", 12, bold=True))
    _lines(c, m, c.px(1.1), ["SPECIMEN - SYNTHETIC TEST DATA"], 9)
    _lines(
        c,
        m,
        c.px(1.5),
        ["Permanent Account Number Card", pan, "Name", name, "Father's Name", father,
         "Date of Birth", dob.strftime("%d/%m/%Y")],
        11,
    )  # fmt: skip
    fields: dict[str, str | None] = {
        "pan": pan,
        "name": name,
        "father_name": father,
        "date_of_birth": dob.isoformat(),
    }
    return c.array(), fields, 0


def aadhaar_card(rng: random.Random, dpi: int) -> tuple[Gray, dict[str, str | None], int]:
    c = Canvas(dpi)
    m = c.px(1.0) + c.px(rng.uniform(0, 0.5))
    name = rng.choice(PEOPLE)[0].title()
    dob = date(1950, 1, 1) + timedelta(days=rng.randrange(20000))
    gender = rng.choice(["Male", "Female"])
    place = rng.choice(PLACES)
    number = _aadhaar(rng)
    spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
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
        [f"Name: {name}", f"DOB: {dob.strftime('%d/%m/%Y')}", f"Gender: {gender}",
         f"Address: {place[4]},", f"{place[2]}, {place[1]} {place[3]}", f"Aadhaar No: {spaced}"],
        11,
    )  # fmt: skip
    fields: dict[str, str | None] = {
        "aadhaar_number": f"XXXX XXXX {number[-4:]}",
        "name": name,
        "date_of_birth": dob.isoformat(),
        "gender": gender.lower(),
        "address": f"{place[4]}, {place[2]}, {place[1]} {place[3]}",
        "pincode": None,
    }
    return c.array(), fields, 0


def build(out_dir: Path, per_type: int = 60, seed: int = 20261023) -> list[CalibrationSample]:
    """``per_type`` documents of each type; every third one goes to the test split."""
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[CalibrationSample] = []
    tables = {"gst_invoice": "line_items", "receipt": "items", "bank_statement": "transactions"}
    for type_index, kind in enumerate(TYPES):
        for i in range(per_type):
            sample_seed = seed + type_index * 100_000 + i
            rng = random.Random(sample_seed)  # noqa: S311  # nosec B311 - synthetic data, not security
            condition, dpi, _ = rng.choices(CONDITIONS, weights=[w for *_, w in CONDITIONS])[0]
            language = "en"
            if kind == "gst_invoice":
                image, fields, rows = gst_invoice(rng, dpi)
            elif kind == "receipt":
                language = "hi" if rng.random() < 0.25 else "en"
                image, fields, rows = receipt(rng, dpi, language)
            elif kind == "bank_statement":
                image, fields, rows = bank_statement(rng, dpi)
            elif kind == "pan_card":
                image, fields, rows = pan_card(rng, dpi)
            else:
                image, fields, rows = aadhaar_card(rng, dpi)
            if condition == "scan":
                image = degrade_scan(image, seed=sample_seed)
            elif condition == "heavy":
                image = degrade_heavy(image, seed=sample_seed)
            sample = CalibrationSample(
                id=f"{kind}-{i:03d}-{condition}",
                document_type=kind,
                language=language,
                condition=condition,
                image=f"{kind}-{i:03d}.png",
                fields=fields,
                tables={tables[kind]: rows} if kind in tables else {},
                split="test" if i % 3 == 2 else "calibration",
                seed=sample_seed,
            )
            buffer = io.BytesIO()
            Image.fromarray(image).save(buffer, "PNG", dpi=(dpi, dpi))
            (out_dir / sample.image).write_bytes(buffer.getvalue())
            samples.append(sample)
    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(s) for s in samples], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return samples


def load_manifest(corpus_dir: Path) -> list[CalibrationSample]:
    data = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    return [CalibrationSample(**item) for item in data]
