"""Classification, field and table extraction, checks and validation (no models)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from newgen_ocr.extraction.checks import run_checks
from newgen_ocr.extraction.classify import classify
from newgen_ocr.extraction.extract import Options, extract_fields
from newgen_ocr.extraction.models import (
    CandidateMethod,
    CheckStatus,
    ClassificationStatus,
    FieldResult,
    FieldStatus,
)
from newgen_ocr.extraction.schema import (
    BUILTIN_DIR,
    DocumentSchema,
    SchemaRegistry,
)
from newgen_ocr.extraction.validation import validate
from newgen_ocr.layout.models import TableStatus
from tests.fixtures.extraction import INVOICE, ITEMS, table, view

REGISTRY = SchemaRegistry()
GST = REGISTRY.schemas["gst_invoice"]
TODAY = date(2024, 6, 1)


def _fields(
    schema: DocumentSchema, blocks: list[list[str]], **kw: object
) -> dict[str, FieldResult]:
    fields, _ = extract_fields(schema, view(blocks, kw.get("tables")), Options(), None)  # type: ignore[arg-type]
    return {f.name: f for f in fields}


# ------------------------------------------------------------ schemas


def test_builtin_schemas_load() -> None:
    assert set(REGISTRY.schemas) == {
        "gst_invoice",
        "receipt",
        "bank_statement",
        "pan_card",
        "aadhaar_card",
    }


def _schema(**overrides: object) -> dict[str, object]:
    base = json.loads((BUILTIN_DIR / "receipt.json").read_text())
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad",
    [
        {"fields": [{"name": "a", "type": "text"}, {"name": "a", "type": "text"}]},
        {"fields": [{"name": "a", "type": "text", "section": "nowhere"}]},
        {"fields": [{"name": "a", "type": "text", "block": "first"}]},
        {"fields": [{"name": "a", "type": "identifier", "pattern": "("}]},
        {"checks": [{"id": "x", "kind": "equal", "fields": ["total"]}]},
        {"checks": [{"id": "x", "kind": "equal", "fields": ["total", "nope"]}]},
        {"checks": [{"id": "x", "kind": "row_product", "table": "items", "target": "amount"}]},
        {
            "checks": [
                {"id": "x", "kind": "column_sum", "table": "nope", "column": "a", "target": "total"}
            ]
        },
        {"checks": [{"id": "x", "kind": "not_future", "fields": []}]},
        {"templates": [{"id": "t", "anchors": ["a"], "regions": {"nope": [0, 0, 1, 1]}}]},
    ],
)
def test_invalid_schemas_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DocumentSchema.model_validate(_schema(**bad))


def test_custom_schema_dir(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text(json.dumps(_schema(id="custom_receipt")))
    assert "custom_receipt" in SchemaRegistry(tmp_path).schemas
    (tmp_path / "y.json").write_text(json.dumps(_schema()))  # clashes with built-in "receipt"
    with pytest.raises(ValueError, match="duplicate schema id"):
        SchemaRegistry(tmp_path)


# ------------------------------------------------------------ classification


def test_classification_by_rules() -> None:
    result = classify(REGISTRY.schemas, view(INVOICE))
    assert (result.status, result.document_type) == (ClassificationStatus.CLASSIFIED, "gst_invoice")
    assert "keyword:tax invoice" in result.evidence["gst_invoice"]
    unknown = classify(REGISTRY.schemas, view([["Dear customer, our office is closed."]]))
    assert (unknown.status, unknown.document_type) == (ClassificationStatus.UNKNOWN, None)
    requested = classify(REGISTRY.schemas, view(INVOICE), requested="receipt")
    assert (requested.status, requested.document_type) == (
        ClassificationStatus.REQUESTED,
        "receipt",
    )


def test_ambiguous_classification_is_not_guessed() -> None:
    mixed = view(
        [
            ["CASH RECEIPT", "Cash Memo", "Payment Mode: UPI", "Account Statement"],
            ["Opening Balance: 1", "Closing Balance: 2"],
        ]
    )
    result = classify(REGISTRY.schemas, mixed)
    assert result.status is ClassificationStatus.AMBIGUOUS and result.document_type is None


def test_template_classification_and_regions() -> None:
    schema = DocumentSchema.model_validate(
        _schema(
            id="acme_receipt",
            templates=[
                {
                    "id": "acme",
                    "anchors": ["acme stores", "thank you"],
                    "regions": {"total": [0.0, 0.0, 1.0, 0.1]},
                }
            ],
        )
    )
    blocks = [["ACME Stores 950.00"], ["Thank you"]]
    result = classify({"acme_receipt": schema}, view(blocks))
    assert (result.status, result.template) == (ClassificationStatus.TEMPLATE, "acme")
    fields, _ = extract_fields(schema, view(blocks), Options(), "acme")
    total = next(f for f in fields if f.name == "total")
    assert (total.status, total.value, total.method) == (
        FieldStatus.EXTRACTED,
        "950.00",
        CandidateMethod.TEMPLATE,
    )


# ------------------------------------------------------------ fields


def test_invoice_fields_with_sections_and_evidence() -> None:
    f = _fields(GST, INVOICE)
    assert f["invoice_number"].value == "INV-7"
    assert f["invoice_date"].value == "2024-03-14"
    assert f["invoice_date"].notes == ["numeric date read day-first"]
    assert f["seller_gstin"].value == "27AAACN1234A1Z7"
    assert f["buyer_gstin"].value == "27AABCS5678K1ZS"
    assert (f["seller_name"].value, f["seller_name"].method) == (
        "Northwind Supplies",
        CandidateMethod.SECTION,
    )
    assert f["buyer_address"].value == "45 FC Road, Pune 411004"
    assert f["grand_total"].value == "1180.00"
    evidence = f["grand_total"].evidence[0]
    assert (evidence.page_number, evidence.line_indices, evidence.text) == (
        1,
        [14],
        "Grand Total: 1,180.00",
    )
    assert f["grand_total"].confidence == 0.95
    for name in ("due_date", "igst_amount", "round_off", "place_of_supply"):
        assert (f[name].status, f[name].value) == (FieldStatus.MISSING, None)


def test_longest_label_wins_and_later_labels_need_separators() -> None:
    f = _fields(
        GST, [["Invoice No: A1 Date: 02/03/2024"], ["Sub Total: 100.00", "Grand Total: 118.00"]]
    )
    assert f["invoice_number"].value == "A1" and f["invoice_date"].value == "2024-03-02"
    assert f["taxable_value"].value == "100.00"  # "sub total", not "total"
    bank = REGISTRY.schemas["bank_statement"]
    b = _fields(bank, [["Bank Name: Coastal Co-operative Bank"]])
    assert b["bank_name"].value == "Coastal Co-operative Bank"  # trailing "Bank" is not a label


def test_unattributed_gstin_is_ambiguous_not_guessed() -> None:
    f = _fields(GST, [["TAX INVOICE"], ["GSTIN: 27AAACN1234A1Z7"]])
    for name in ("seller_gstin", "buyer_gstin"):
        assert f[name].status is FieldStatus.AMBIGUOUS and f[name].value is None
        assert f[name].candidates[0].method is CandidateMethod.UNATTRIBUTED


def test_conflicting_values_are_not_resolved() -> None:
    f = _fields(GST, [["Invoice No: A1"], ["Invoice Number: B2"]])
    assert f["invoice_number"].status is FieldStatus.CONFLICT
    assert f["invoice_number"].value is None
    assert len(f["invoice_number"].candidates) == 2


def test_invalid_and_ambiguous_values() -> None:
    f = _fields(GST, [["Seller:", "GSTIN: 27AAACN1234A1Z9"], ["Invoice Date: 31/02/2024"]])
    assert f["seller_gstin"].status is FieldStatus.INVALID
    assert f["seller_gstin"].raw == "27AAACN1234A1Z9"
    assert "GSTIN check character does not match" in f["seller_gstin"].errors
    assert f["invoice_date"].status is FieldStatus.INVALID
    g = _fields(GST, [["Grand Total: 10 11800.00"]])
    assert g["grand_total"].status is FieldStatus.AMBIGUOUS
    assert "possible values: 10.00, 11800.00" in g["grand_total"].notes


def test_label_without_a_value_of_its_type_is_not_a_candidate() -> None:
    pan = REGISTRY.schemas["pan_card"]
    f = _fields(pan, [["Permanent Account Number Card", "ABCPK1234F"]])
    assert (f["pan"].status, f["pan"].value, f["pan"].method) == (
        FieldStatus.EXTRACTED,
        "ABCPK1234F",
        CandidateMethod.PATTERN,
    )


def test_value_on_following_lines() -> None:
    pan = REGISTRY.schemas["pan_card"]
    f = _fields(
        pan,
        [["Name", "RAVI KUMAR", "Father's Name", "SURESH KUMAR", "Date of Birth", "15/08/1990"]],
    )
    assert (f["name"].value, f["father_name"].value) == ("RAVI KUMAR", "SURESH KUMAR")
    assert f["date_of_birth"].value == "1990-08-15"
    aadhaar = REGISTRY.schemas["aadhaar_card"]
    a = _fields(
        aadhaar, [["Address: 14 Lake View Road,", "Kolkata 700029", "Aadhaar No: 2468 1357 9026"]]
    )
    assert a["address"].value == "14 Lake View Road, Kolkata 700029"
    assert a["aadhaar_number"].value == "XXXX XXXX 9026"


def test_multilingual_labels() -> None:
    receipt = REGISTRY.schemas["receipt"]
    digits = "".join(chr(0x0966 + int(d)) if d.isdigit() else d for d in "05/04/2024")
    amount = "".join(chr(0x0966 + int(d)) if d.isdigit() else d for d in "850.00")
    f = _fields(receipt, [["रसीद"], [f"दिनांक: {digits}", f"कुल: {amount}"]])  # Devanagari digits
    assert f["receipt_date"].value == "2024-04-05"
    assert f["total"].value == "850.00"


# ------------------------------------------------------------ tables


def test_table_records_with_summary_rows_and_evidence() -> None:
    rows = [*ITEMS, ["Total", "", "", "", "1000.00"]]
    _, tables = extract_fields(GST, view(INVOICE, [table(rows)]), Options(), None)
    items = tables[0]
    assert items.status == "extracted"
    assert [r.values["description"].value for r in items.records] == ["Paper", "Toner"]
    assert items.records[0].values["amount"].value == "500.00"
    assert items.records[0].values["quantity"].value == "2"
    assert items.records[0].values["tax_rate"].status is FieldStatus.MISSING  # no such column
    assert len(items.summary_rows) == 1
    cell = items.records[1].values["rate"].evidence
    assert cell is not None and (cell.table_id, cell.row, cell.col) == (0, 2, 3)
    assert "amount<-amount" in str(items.sources[0]["mapped"])


def test_tables_without_required_columns_and_bad_cells() -> None:
    _, tables = extract_fields(
        GST, view(INVOICE, [table([["Name", "Phone"], ["a", "1"]])]), Options(), None
    )
    assert tables[0].status == "not_found" and tables[0].issues
    bad = [ITEMS[0], ["Paper", "4802", "two", "250.00", "5,00.00"]]
    _, tables = extract_fields(
        GST, view(INVOICE, [table(bad, status=TableStatus.NEEDS_REVIEW)]), Options(), None
    )
    record = tables[0].records[0]
    assert record.values["quantity"].status is FieldStatus.INVALID
    assert record.values["amount"].status is FieldStatus.INVALID
    assert tables[0].issues == ["page 1 table 0: layout routed it to review"]


def test_label_rows_of_tables_are_field_candidates() -> None:
    totals = table(
        [["Taxable Value", "4,500.00"], ["CGST 6%", "270.00"], ["Total Amount", "5,040.00"]],
        header=False,
    )
    f = _fields(GST, [["TAX INVOICE"]], tables=[totals])
    assert f["taxable_value"].value == "4500.00"
    assert (f["cgst_amount"].value, f["cgst_amount"].method) == (
        "270.00",
        CandidateMethod.TABLE_ROW,
    )
    assert f["grand_total"].value == "5040.00"


# ------------------------------------------------------------ checks and validation


def _run(
    schema: DocumentSchema, blocks: list[list[str]], rows: list[list[str]] | None = None
) -> dict[str, CheckStatus]:
    fields, tables = extract_fields(
        schema, view(blocks, [table(rows)] if rows else []), Options(), None
    )
    return {c.id: c.status for c in run_checks(schema, fields, tables, TODAY)}


def test_invoice_checks_pass_and_fail() -> None:
    ok = _run(GST, INVOICE, ITEMS)
    assert ok["line_amount"] is ok["items_total"] is ok["grand_total"] is CheckStatus.PASSED
    assert ok["cgst_equals_sgst"] is ok["tax_type"] is CheckStatus.PASSED
    assert ok["due_after_invoice"] is CheckStatus.NOT_APPLICABLE  # no due date: nothing assumed
    wrong = [b.copy() for b in INVOICE]
    wrong[4] = ["Taxable Value: 1,000.00", "IGST: 180.00", "Grand Total: 1,190.00"]
    bad = _run(GST, wrong, [ITEMS[0], ["Paper", "4802", "2", "250.00", "600.00"], ITEMS[2]])
    assert bad["line_amount"] is bad["items_total"] is bad["grand_total"] is CheckStatus.FAILED
    assert bad["tax_type"] is CheckStatus.FAILED  # same state, but IGST charged


def test_check_messages_record_assumptions() -> None:
    fields, tables = extract_fields(
        GST, view([["Taxable Value: 100.00", "Grand Total: 118.00"]]), Options(), None
    )
    check = next(c for c in run_checks(GST, fields, tables, TODAY) if c.id == "grand_total")
    assert check.status is CheckStatus.FAILED
    assert check.difference == "18.00"
    assert "counted as 0 because not extracted: cgst_amount" in check.message


def test_bank_checks() -> None:
    bank = REGISTRY.schemas["bank_statement"]
    blocks = [
        [
            "STATEMENT OF ACCOUNT",
            "Account No: 001234567890",
            "From: 01/03/2024 To: 31/03/2024",
            "Opening Balance: 100.00",
            "Closing Balance: 130.00",
        ]
    ]
    rows = [
        ["Date", "Description", "Debit", "Credit", "Balance"],
        ["02/03/2024", "salary", "", "50.00", "150.00"],
        ["05/04/2024", "rent", "20.00", "", "130.00"],
    ]
    result = _run(bank, blocks, rows)
    assert result["balance_reconciles"] is result["running_balance"] is CheckStatus.PASSED
    assert result["period_order"] is CheckStatus.PASSED
    assert result["dates_in_period"] is CheckStatus.FAILED  # 5 April is outside March
    rows[2][4] = "131.00"
    assert _run(bank, blocks, rows)["running_balance"] is CheckStatus.FAILED


def test_other_check_kinds() -> None:
    schema = DocumentSchema.model_validate(
        _schema(
            fields=[
                {"name": "gstin", "type": "gstin", "labels": ["gstin"]},
                {"name": "pan", "type": "pan", "labels": ["pan"]},
                {"name": "total", "type": "amount", "labels": ["total"]},
                {"name": "receipt_date", "type": "date", "labels": ["date"]},
            ],
            tables=[],
            checks=[
                {"id": "pan", "kind": "gstin_pan", "fields": ["gstin", "pan"]},
                {"id": "future", "kind": "not_future", "fields": ["receipt_date"]},
                {
                    "id": "items",
                    "kind": "row_product",
                    "table": "t",
                    "target": "a",
                    "factors": ["b"],
                },
            ],
        )
        | {
            "tables": [
                {
                    "name": "t",
                    "columns": [
                        {"name": "a", "type": "amount", "headers": ["a"]},
                        {"name": "b", "type": "amount", "headers": ["b"]},
                    ],
                }
            ]
        }
    )
    fields, tables = extract_fields(
        schema,
        view([["GSTIN: 27AAPFU0939F1ZV", "PAN: AAPFU0939F", "Date: 01/01/2030"]]),
        Options(),
        None,
    )
    results = {c.id: c for c in run_checks(schema, fields, tables, TODAY)}
    assert results["pan"].status is CheckStatus.PASSED
    assert results["future"].status is CheckStatus.FAILED
    assert results["items"].status is CheckStatus.NOT_APPLICABLE


def test_validation_reports_missing_and_invalid() -> None:
    fields, tables = extract_fields(
        GST, view([["TAX INVOICE"], ["Invoice Date: 31/02/2024"]]), Options(), None
    )
    report, record = validate(GST, fields, tables)
    assert not report.valid
    codes = {(i.code, tuple(i.location)) for i in report.issues}
    assert ("missing_required", ("fields", "invoice_number")) in codes
    assert ("field_invalid", ("fields", "invoice_date")) in codes
    assert record["invoice_number"] is None and record["invoice_date"] is None  # never invented
    assert set(record) == {f.name for f in GST.fields}


def test_validation_of_complete_record_and_rows() -> None:
    fields, tables = extract_fields(GST, view(INVOICE, [table(ITEMS)]), Options(), None)
    report, record = validate(GST, fields, tables)
    assert report.valid, report.issues
    assert record["grand_total"] == "1180.00"
    bad_rows = [ITEMS[0], ["", "4802", "2", "250.00", "500.00"]]
    _, tables = extract_fields(GST, view(INVOICE, [table(bad_rows)]), Options(), None)
    report, _ = validate(GST, fields, tables)
    assert ("missing_required", ("tables", "line_items", 0, "description")) in {
        (i.code, tuple(i.location)) for i in report.issues
    }
    assert Decimal(str(record["taxable_value"])) == Decimal("1000.00")
