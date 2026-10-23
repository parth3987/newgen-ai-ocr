"""Review queue, corrections, approval rules, history chain, patterns and crops."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from newgen_ocr.auth.tokens import Role
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.review.labelled import from_reviews
from newgen_ocr.review.models import (
    ApproveRequest,
    CalibrationKind,
    Change,
    ClaimRequest,
    CorrectionReason,
    CorrectionRequest,
    HistoryKind,
    RejectReason,
    RejectRequest,
    ReviewItem,
    ReviewItemStatus,
    VersionedRequest,
)
from newgen_ocr.review.service import ReviewService
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.extraction import INVOICE, ITEMS, store_document, table
from tests.fixtures.review import TODAY, calibration, extract, review_service, who

ASHA = who("asha", Role.REVIEWER)
MEENA = who("meena", Role.APPROVER)
ADMIN = who("root", Role.ADMIN)
BAD_TOTAL = [
    *INVOICE[:-1],
    ["Taxable Value: 1,000.00", "CGST @ 9%: 90.00", "SGST @ 9%: 90.00", "Grand Total: 1,130.00"],
]


@pytest.fixture
def store(settings: Settings) -> DocumentStore:
    return DocumentStore(settings.data_dir)


def _queued(
    settings: Settings, store: DocumentStore, blocks: list[list[str]] = INVOICE, **kw: object
) -> tuple[ReviewService, ReviewItem]:
    doc = store_document(settings, store, blocks, [table(ITEMS)])
    service = review_service(settings, store, **kw)  # type: ignore[arg-type]
    return service, service.enqueue(extract(settings, store, doc))


def _code(exc: pytest.ExceptionInfo[AppError]) -> ErrorCode:
    return exc.value.code


def test_enqueue_is_idempotent_and_requeues(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    assert item.status is ReviewItemStatus.PENDING and item.revision == 1
    assert "not_calibrated" in item.reasons and item.priority > 0
    again = service.enqueue(store.read_extraction(item.document_id, "auto"))
    assert again == item  # same extraction: same item
    renewed = service.enqueue(extract(settings, store, item.document_id))
    assert (renewed.revision, renewed.version) == (2, 2)
    archived = settings.data_dir / "review" / "items" / item.document_id / "auto.r1.json"
    assert archived.is_file()
    kinds = [e.kind for e in service.history(item.document_id, "auto")]
    assert kinds == [HistoryKind.ENQUEUED, HistoryKind.REQUEUED]
    total, listed = service.queue({ReviewItemStatus.PENDING})
    assert total == 1 and listed[0].fields_to_review == len(item.assessment.fields)


def test_auto_accept_only_when_calibrated_and_enabled(
    settings: Settings, store: DocumentStore
) -> None:
    _, held = _queued(settings, store, cal=calibration())
    assert held.status is ReviewItemStatus.PENDING and held.assessment.eligible
    service, item = _queued(settings, store, cal=calibration(), auto_accept=True)
    assert item.status is ReviewItemStatus.AUTO_ACCEPTED
    assert item.decision is not None and item.decision.actor == "system"
    _, flagged = _queued(settings, store, BAD_TOTAL, cal=calibration(), auto_accept=True)
    assert flagged.status is ReviewItemStatus.PENDING  # a failed check is never auto-accepted
    assert service.queue({ReviewItemStatus.AUTO_ACCEPTED})[0] == 1


def test_claim_correct_approve(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store, BAD_TOTAL, cal=calibration())
    doc = item.document_id
    with pytest.raises(AppError) as exc:  # corrections need a claim
        service.correct(doc, "auto", ASHA, _set(item.version, "grand_total", "1,180.00"))
    assert _code(exc) is ErrorCode.REVIEW_CONFLICT
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    assert item.status is ReviewItemStatus.IN_REVIEW and item.assigned_to == "asha"
    with pytest.raises(AppError) as exc:  # stale version
        service.correct(doc, "auto", ASHA, _set(item.version - 1, "grand_total", "1,180.00"))
    assert _code(exc) is ErrorCode.REVIEW_CONFLICT
    with pytest.raises(AppError) as exc:  # invalid value, normalised like an extracted one
        service.correct(doc, "auto", ASHA, _set(item.version, "seller_gstin", "27AAACN1234A1Z8"))
    assert _code(exc) is ErrorCode.INVALID_CORRECTION and "check character" in exc.value.message
    item = service.correct(doc, "auto", ASHA, _set(item.version, "grand_total", "₹1,180.00"))
    fix = item.corrections["fields.grand_total"]
    assert (fix.value, fix.previous, fix.actor) == ("1180.00", "1130.00", "asha")
    result = service.result(doc, "auto", today=TODAY)
    assert result.record["grand_total"] == "1180.00"
    assert {c.id: c.status.value for c in result.checks}["grand_total"] == "passed"
    assert result.fields["grand_total"].source == "corrected"
    # The other terms of the failed check still need a person's decision (confirm or fix).
    assert result.unresolved == ["fields.taxable_value", "fields.cgst_amount", "fields.sgst_amount"]

    with pytest.raises(AppError) as exc:  # unresolved fields remain
        service.approve(doc, "auto", MEENA, ApproveRequest(expected_version=item.version))
    assert _code(exc) is ErrorCode.REVIEW_INCOMPLETE
    item = _resolve_all(service, item)
    with pytest.raises(AppError) as exc:  # four eyes: asha corrected, asha cannot approve
        service.approve(
            doc, "auto", who("asha", Role.APPROVER), ApproveRequest(expected_version=item.version)
        )
    assert _code(exc) is ErrorCode.FORBIDDEN
    item = service.approve(
        doc, "auto", MEENA, ApproveRequest(expected_version=item.version, comment="ok"), TODAY
    )
    assert item.status is ReviewItemStatus.APPROVED and item.decision is not None
    assert item.decision.actor == "meena" and item.assigned_to is None
    with pytest.raises(AppError) as exc:
        service.reject(
            doc,
            "auto",
            ASHA,
            RejectRequest(expected_version=item.version, reason=RejectReason.DUPLICATE),
        )
    assert _code(exc) is ErrorCode.REVIEW_CONFLICT  # decided items are closed

    history = service.history(doc, "auto")
    corrected = [e for e in history if e.kind is HistoryKind.CORRECTED]
    total = next(e for e in corrected if e.location == "fields.grand_total")
    assert (total.extracted_value, total.new_value, total.reason) == (
        "1130.00",
        "1180.00",
        "ocr_misread",
    )
    assert total.actor == "asha" and total.input_text == "₹1,180.00"
    assert item.corrections["fields.grand_total"].seq == total.seq
    assert service.verify().valid


def _set(version: int, field: str, value: str) -> CorrectionRequest:
    return CorrectionRequest(
        expected_version=version,
        changes=[Change(field=field, value=value, reason=CorrectionReason.OCR_MISREAD)],
    )


def _resolve_all(service: ReviewService, item: ReviewItem) -> ReviewItem:
    result = service.result(item.document_id, "auto", today=TODAY)
    changes = []
    for location in result.unresolved:
        kind, _, name = location.partition(".")
        if kind == "fields":
            changes.append(Change(field=name, action="confirm"))
        else:
            changes.append(Change(table=name, action="confirm"))
    if not changes:
        return item
    return service.correct(
        item.document_id,
        "auto",
        ASHA,
        CorrectionRequest(expected_version=item.version, changes=changes),
    )


def test_failed_checks_need_acknowledgement(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store, BAD_TOTAL)
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    item = _resolve_all(service, item)  # confirms the wrong total as printed
    with pytest.raises(AppError) as exc:
        service.approve(doc, "auto", MEENA, ApproveRequest(expected_version=item.version), TODAY)
    assert "grand_total" in exc.value.message
    with pytest.raises(AppError):  # acknowledged, but without a comment
        service.approve(
            doc,
            "auto",
            MEENA,
            ApproveRequest(
                expected_version=item.version, acknowledge_failed_checks=["grand_total"]
            ),
            TODAY,
        )
    item = service.approve(
        doc,
        "auto",
        MEENA,
        ApproveRequest(
            expected_version=item.version,
            acknowledge_failed_checks=["grand_total"],
            comment="printed total is wrong on the original",
        ),
        TODAY,
    )
    assert item.decision is not None and item.decision.acknowledged_checks == ["grand_total"]


def test_claims_release_reject_and_admin(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    other = who("ravi", Role.REVIEWER)
    with pytest.raises(AppError):
        service.claim(doc, "auto", other, ClaimRequest(expected_version=item.version))
    with pytest.raises(AppError):
        service.release(doc, "auto", other, VersionedRequest(expected_version=item.version))
    with pytest.raises(AppError):  # only an admin may take over, and only with force
        service.claim(doc, "auto", ADMIN, ClaimRequest(expected_version=item.version))
    item = service.claim(
        doc, "auto", ADMIN, ClaimRequest(expected_version=item.version, force=True)
    )
    assert item.assigned_to == "root"
    item = service.release(doc, "auto", ADMIN, VersionedRequest(expected_version=item.version))
    assert item.status is ReviewItemStatus.PENDING and item.assigned_to is None
    item = service.reject(
        doc,
        "auto",
        other,
        RejectRequest(
            expected_version=item.version, reason=RejectReason.ILLEGIBLE, comment="faded"
        ),
    )
    assert item.status is ReviewItemStatus.REJECTED and item.decision is not None
    assert item.decision.reason == "illegible"


def test_change_validation() -> None:
    with pytest.raises(ValueError, match="reason"):
        Change(field="x", value="1")
    with pytest.raises(ValueError, match="comment"):
        Change(field="x", value="1", reason=CorrectionReason.OTHER)
    with pytest.raises(ValueError, match="either"):
        Change(field="x", table="t", action="confirm")
    with pytest.raises(ValueError, match="clear"):
        Change(field="x", value=" ", reason=CorrectionReason.OCR_MISREAD)
    with pytest.raises(ValueError, match="only be confirmed"):
        Change(table="t", action="clear", reason=CorrectionReason.VALUE_NOT_PRESENT)
    with pytest.raises(ValueError, match="no value"):
        Change(field="x", action="confirm", value="1")
    assert (
        Change(
            table="t", record=1, column="c", value="2", reason=CorrectionReason.OCR_MISREAD
        ).location
        == "tables.t.1.c"
    )


def test_cell_and_clear_corrections(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    item = service.correct(
        doc,
        "auto",
        ASHA,
        CorrectionRequest(
            expected_version=item.version,
            changes=[
                Change(
                    table="line_items",
                    record=1,
                    column="quantity",
                    value="2",
                    reason=CorrectionReason.OCR_MISREAD,
                ),
                Change(
                    field="buyer_gstin", action="clear", reason=CorrectionReason.VALUE_NOT_PRESENT
                ),
            ],
        ),
    )
    result = service.result(doc, "auto", today=TODAY)
    assert result.tables["line_items"][1]["quantity"] == "2"
    assert {c.id: c.status.value for c in result.checks}["line_amount"] == "failed"
    assert result.record["buyer_gstin"] is None and result.fields["buyer_gstin"].source == "cleared"
    for bad in (
        Change(
            table="line_items",
            record=9,
            column="quantity",
            value="1",
            reason=CorrectionReason.OCR_MISREAD,
        ),
        Change(field="nope", value="1", reason=CorrectionReason.OCR_MISREAD),
        Change(table="nope", action="confirm"),
        Change(field="invoice_date", value="31/02/2024", reason=CorrectionReason.OCR_MISREAD),
    ):
        with pytest.raises(AppError) as exc:
            service.correct(
                doc, "auto", ASHA, CorrectionRequest(expected_version=item.version, changes=[bad])
            )
        assert _code(exc) is ErrorCode.INVALID_CORRECTION


def test_aadhaar_corrections_are_masked(settings: Settings, store: DocumentStore) -> None:
    blocks = [
        ["Government of India", "Unique Identification Authority of India"],
        ["Name: Anita Desai", "DOB: 02/11/1985", "Aadhaar No: 2468 1357 9027"],
    ]
    service, item = _queued(settings, store, blocks)
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    item = service.correct(
        doc, "auto", ASHA, _set(item.version, "aadhaar_number", "2468 1357 9026")
    )
    assert item.corrections["fields.aadhaar_number"].value == "XXXX XXXX 9026"
    raw = (settings.data_dir / "review" / "history.jsonl").read_text()
    assert "2468 1357" not in raw and "24681357" not in raw
    png = service.page(doc, "auto", 1, "aadhaar_number")
    assert Image.open(io.BytesIO(png)).size[0] <= 1400


def test_history_chain_detects_tampering(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    service.claim(item.document_id, "auto", ASHA, ClaimRequest(expected_version=item.version))
    path = settings.data_dir / "review" / "history.jsonl"
    lines = path.read_text().splitlines()
    assert service.verify().valid and service.verify().events == 2
    event = json.loads(lines[0])
    event["actor"] = "mallory"
    path.write_text(json.dumps(event) + "\n" + lines[1] + "\n")
    broken = service.verify()
    assert not broken.valid and broken.first_bad_seq == 1
    path.write_text(lines[1] + "\n")  # an event removed
    assert not service.verify().valid


def test_patterns(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store, BAD_TOTAL, cal=calibration())
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    item = service.correct(doc, "auto", ASHA, _set(item.version, "grand_total", "1,180.00"))
    service.correct(
        doc,
        "auto",
        ASHA,
        CorrectionRequest(
            expected_version=item.version,
            changes=[Change(field="invoice_number", action="confirm")],
        ),
    )
    patterns = service.patterns()
    rows = {r["location"]: r for r in patterns.by_field}
    assert (
        rows["fields.grand_total"]["changed"] == 1 and rows["fields.invoice_number"]["changed"] == 0
    )
    assert patterns.by_reason == {"ocr_misread": 1, "confirmed": 1}
    assert {"read": "3", "correct": "8", "count": 1} in patterns.character_confusions
    assert patterns.items_decided == {"in_review": 1}
    # invoice_number was verified (calibrated, clean); confirming it is not a failure.
    assert patterns.verified_but_corrected == []


def test_crops(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    doc = item.document_id
    crop = Image.open(io.BytesIO(service.crop(doc, "auto", field="grand_total")))
    assert crop.width < 1000 and crop.height < 400
    assert Image.open(
        io.BytesIO(service.crop(doc, "auto", table="line_items", record=0, column="amount"))
    )
    assert Image.open(io.BytesIO(service.crop(doc, "auto", field="seller_gstin", candidate=0)))
    page = Image.open(io.BytesIO(service.page(doc, "auto", 1, "grand_total")))
    assert page.size == (1000, 1400)
    for kwargs in (
        {"field": "due_date"},
        {"field": "nope"},
        {"field": "seller_gstin", "candidate": 9},
        {"table": "line_items", "record": 7, "column": "amount"},
    ):
        with pytest.raises(AppError) as exc:
            service.crop(doc, "auto", **kwargs)
        assert _code(exc) is ErrorCode.EVIDENCE_NOT_FOUND


def test_out_of_date_item(settings: Settings, store: DocumentStore) -> None:
    service, item = _queued(settings, store)
    extract(settings, store, item.document_id)  # extracted again, not re-queued
    with pytest.raises(AppError) as exc:
        service.result(item.document_id, "auto")
    assert _code(exc) is ErrorCode.REVIEW_CONFLICT
    with pytest.raises(AppError) as exc:
        service.item(item.document_id, "other")
    assert _code(exc) is ErrorCode.REVIEW_ITEM_NOT_FOUND


def test_approved_reviews_become_labelled_documents(
    settings: Settings, store: DocumentStore
) -> None:
    service, item = _queued(settings, store, BAD_TOTAL, cal=calibration())
    doc = item.document_id
    item = service.claim(doc, "auto", ASHA, ClaimRequest(expected_version=item.version))
    item = service.correct(doc, "auto", ASHA, _set(item.version, "grand_total", "1,180.00"))
    item = _resolve_all(service, item)
    service.approve(doc, "auto", MEENA, ApproveRequest(expected_version=item.version), TODAY)
    _queued(settings, store)  # pending: not ground truth
    docs = from_reviews(settings.data_dir)
    assert len(docs) == 1 and docs[0].sample_id == f"{doc}/auto"
    by_field = {o.field: o for o in docs[0].observations}
    assert not by_field["grand_total"].correct  # extracted 1130.00, approved 1180.00
    assert by_field["invoice_number"].correct and by_field["invoice_number"].gated
    assert by_field["due_date"].kind is CalibrationKind.MISSING and by_field["due_date"].correct
