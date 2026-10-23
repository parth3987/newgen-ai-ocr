"""Field-specific gates: what is verified and why the rest goes to a person."""

from __future__ import annotations

import pytest

from newgen_ocr.config import Settings
from newgen_ocr.extraction.models import CheckStatus, ExtractionResult
from newgen_ocr.layout.models import LayoutReviewReason
from newgen_ocr.ocr.models import PageReview, ReviewReason
from newgen_ocr.review.acceptance import check_fields
from newgen_ocr.review.models import (
    Assessment,
    EngineAgreement,
    FieldDecision,
    FieldReviewReason,
)
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.extraction import INVOICE, ITEMS, store_document, table
from tests.fixtures.review import calibration, extract, review_service, second_engine

R = FieldReviewReason


@pytest.fixture
def store(settings: Settings) -> DocumentStore:
    return DocumentStore(settings.data_dir)


def _assess(settings: Settings, store: DocumentStore, doc: str, **kw: object) -> Assessment:
    result = extract(settings, store, doc)
    return review_service(settings, store, **kw).assess(result)  # type: ignore[arg-type]


def _fields(a: Assessment) -> dict[str, FieldDecision]:
    return {d.field: d for d in a.fields}


def test_without_calibration_nothing_is_verified(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    a = _assess(settings, store, doc)
    assert a.calibration_id is None and not a.eligible
    total = _fields(a)["grand_total"]
    assert total.reasons == [R.NOT_CALIBRATED]
    assert total.confidence == 0.95  # high OCR confidence alone verifies nothing
    assert not total.verified


def test_calibrated_clean_invoice_is_eligible(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    a = _assess(settings, store, doc, cal=calibration())
    assert a.eligible, [(d.field, d.reasons) for d in a.fields if not d.verified]
    total = _fields(a)["grand_total"]
    assert total.threshold == 0.9 and total.calibration_key == "value:type:amount"
    assert "grand_total" in total.checks_passed
    items = a.tables[0]
    assert items.verified and set(items.checks_passed) == {"line_amount", "items_total"}


def test_below_threshold_and_missing(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    fields = _fields(_assess(settings, store, doc, cal=calibration(threshold=0.97, missing=False)))
    assert R.BELOW_THRESHOLD in fields["grand_total"].reasons
    due = fields["due_date"]
    assert due.reasons == [R.MISSING_UNVERIFIED]


def test_failed_check_routes_its_fields(settings: Settings, store: DocumentStore) -> None:
    blocks = [
        *INVOICE[:-1],
        [
            "Taxable Value: 1,000.00",
            "CGST @ 9%: 90.00",
            "SGST @ 9%: 90.00",
            "Grand Total: 1,190.00",
        ],
    ]
    doc = store_document(settings, store, blocks, [table(ITEMS)])
    a = _assess(settings, store, doc, cal=calibration())
    total = _fields(a)["grand_total"]
    assert R.CHECK_FAILED in total.reasons and total.checks_failed == ["grand_total"]
    assert "check_failed" in a.reasons and not a.eligible


def test_engine_disagreement(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    second_engine(store, doc, {"1,180.00": "1,130.00", "INV-7": "INV-7"})
    a = _assess(settings, store, doc, cal=calibration())
    fields = _fields(a)
    total, number = fields["grand_total"], fields["invoice_number"]
    assert total.engine.status is EngineAgreement.DISAGREE
    assert total.engine.readings == ["1130.00"] and R.ENGINE_DISAGREEMENT in total.reasons
    assert number.engine.status is EngineAgreement.AGREE and number.verified
    assert not a.eligible


def test_source_flags(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    ocr = store.read_ocr_run(doc, "auto")
    # Low-confidence lines elsewhere do not block a value: its own confidence is calibrated.
    ocr.pages[0].review = PageReview(needs_review=True, reasons=[ReviewReason.LOW_CONFIDENCE_LINES])
    store.write_ocr_run(doc, "auto", ocr)
    assert _assess(settings, store, doc, cal=calibration()).eligible
    layout = store.read_layout_run(doc, "auto")
    layout.pages[0].review.reasons = [LayoutReviewReason.TABLE_STRUCTURE_MISMATCH]
    store.write_layout_run(doc, layout)
    a = _assess(settings, store, doc, cal=calibration())
    assert _fields(a)["grand_total"].verified
    assert not a.tables[0].verified and "source_flagged" in a.tables[0].reasons
    ocr.pages[0].review = PageReview(needs_review=True, reasons=[ReviewReason.LOW_QUALITY])
    store.write_ocr_run(doc, "auto", ocr)
    fields = _fields(_assess(settings, store, doc, cal=calibration()))
    assert R.SOURCE_FLAGGED in fields["grand_total"].reasons
    # A value reported absent on a flagged page may simply not have been read.
    assert fields["due_date"].reasons == [R.SOURCE_FLAGGED]


def test_table_cells_and_corroboration(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    second_engine(store, doc, {"Toner": "Tonor", "250.00": "260.00"})
    items = _assess(settings, store, doc, cal=calibration()).tables[0]
    # The rate disagrees but quantity x rate = amount passes on every row: corroborated.
    assert items.engine_disagreements_corroborated == 1
    assert items.cells_flagged == ["1.description"] and not items.verified


def test_unclassified(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, [["Dear customer, we are closed on Monday."]])
    a = _assess(settings, store, doc, cal=calibration())
    assert a.reasons == ["document_type_unknown"] and not a.fields and not a.eligible


def test_check_fields_cover_terms_and_targets() -> None:
    from newgen_ocr.extraction.schema import SchemaRegistry

    schema = SchemaRegistry().get("gst_invoice")
    assert schema is not None
    by_id = {c.id: check_fields(c) for c in schema.checks}
    assert by_id["grand_total"] >= {"grand_total", "taxable_value", "cgst_amount"}
    assert by_id["items_total"] == {"taxable_value"}
    assert by_id["line_amount"] == set()  # row checks concern table columns only


def test_result_type(settings: Settings, store: DocumentStore) -> None:
    doc = store_document(settings, store, INVOICE, [table(ITEMS)])
    result: ExtractionResult = extract(settings, store, doc)
    assert {c.status for c in result.checks} <= {CheckStatus.PASSED, CheckStatus.NOT_APPLICABLE}
