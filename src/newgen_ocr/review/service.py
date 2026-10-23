"""Review queue, corrections, approval and correction patterns (ADR-0028; docs/REVIEW.md).

Every extraction result becomes a review item. With automatic acceptance off (the default)
every item waits for a person; verified values are marked so reviewers can concentrate on
the rest. With it on, an item whose every value passes its calibrated gates is accepted
without a person, and the acceptance is recorded like any other decision.

Workflow: claim → correct / confirm → approve (an approver, by default not the person who
corrected) or reject. Every step is checked against the item's ``version`` (optimistic
concurrency), appended to the hash-chained history, and audited without content.
"""

from __future__ import annotations

import difflib
import logging
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
from PIL import Image

from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction.extract import mask_text
from newgen_ocr.extraction.models import Evidence, ExtractionResult
from newgen_ocr.extraction.schema import FieldType, SchemaRegistry
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.layout.service import ocr_page_image
from newgen_ocr.logging_config import request_id_var
from newgen_ocr.ocr.models import OcrRun
from newgen_ocr.review import crops
from newgen_ocr.review.acceptance import Policy, assess
from newgen_ocr.review.corrections import new_value, reviewed, target, unresolved
from newgen_ocr.review.engines import comparable_pages, secondary_run
from newgen_ocr.review.models import (
    OPEN,
    AppliedCorrection,
    ApproveRequest,
    Assessment,
    ChainVerification,
    ClaimRequest,
    CorrectionPatterns,
    CorrectionReason,
    CorrectionRequest,
    Decision,
    FieldReviewReason,
    HistoryEvent,
    HistoryKind,
    RejectRequest,
    ReviewedResult,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemSummary,
    VersionedRequest,
)
from newgen_ocr.review.store import ReviewStore
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

SYSTEM = "system"
_PRIORITY = {
    FieldReviewReason.REQUIRED_MISSING: 30,
    FieldReviewReason.CHECK_FAILED: 20,
    FieldReviewReason.CONFLICT: 15,
    FieldReviewReason.INVALID: 15,
    FieldReviewReason.ENGINE_DISAGREEMENT: 15,
    FieldReviewReason.AMBIGUOUS: 10,
    FieldReviewReason.SOURCE_FLAGGED: 10,
    FieldReviewReason.BELOW_THRESHOLD: 5,
}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def priority(assessment: Assessment) -> int:
    score = 100 if any(r.startswith("document_type") for r in assessment.reasons) else 0
    for d in assessment.fields:
        score += max((_PRIORITY.get(r, 1) for r in d.reasons), default=0)
    score += sum(10 for t in assessment.tables if not t.verified)
    return score


class ReviewService:
    def __init__(
        self,
        store: DocumentStore,
        reviews: ReviewStore,
        audit: AuditLog,
        registry: SchemaRegistry,
        policy: Policy,
        require_second_person: bool = True,
    ) -> None:
        self.store = store
        self.reviews = reviews
        self.audit = audit
        self.registry = registry
        self.policy = policy
        self.require_second_person = require_second_person

    # ------------------------------------------------------------ assessment and queue

    def assess(self, result: ExtractionResult) -> Assessment:
        schema = self.registry.get(result.schema_id or "")
        ocr: OcrRun | None = None
        layout: LayoutRun | None = None
        try:
            layout = self.store.read_layout_run(result.document_id, result.layout_run)
            ocr = self.store.read_ocr_run(result.document_id, result.ocr_run)
        except AppError:
            logger.warning("review: source runs missing", extra={"document_id": result.document_id})
        other = secondary_run(self.store, result.document_id, result.ocr_run) if ocr else None
        return assess(
            result,
            schema,
            self.policy,
            ocr,
            layout,
            comparable_pages(ocr, other) if ocr else {},
            other.requested_engine if other else None,
        )

    def _event(
        self, item: ReviewItem, kind: HistoryKind, actor: str, **fields: Any
    ) -> dict[str, Any]:
        return {
            "ts": _now(),
            "kind": kind,
            "actor": actor,
            "request_id": request_id_var.get(),
            "document_id": item.document_id,
            "layout_run": item.layout_run,
            "schema_id": item.schema_id,
            "revision": item.revision,
            "version": item.version,
            **fields,
        }

    def enqueue(self, result: ExtractionResult) -> ReviewItem:
        """Create (or renew, after a new extraction) the review item for a result."""
        assessment = self.assess(result)
        now = _now()
        with self.reviews.locked():
            existing = self.reviews.get(result.document_id, result.layout_run)
            if existing is not None and existing.extraction_created_at == result.created_at:
                return existing
            auto = self.policy.auto_accept and assessment.eligible
            item = ReviewItem(
                document_id=result.document_id,
                layout_run=result.layout_run,
                schema_id=result.schema_id,
                schema_version=result.schema_version,
                extraction_created_at=result.created_at,
                revision=existing.revision + 1 if existing else 1,
                version=existing.version + 1 if existing else 1,
                status=ReviewItemStatus.AUTO_ACCEPTED if auto else ReviewItemStatus.PENDING,
                priority=priority(assessment),
                reasons=list(
                    dict.fromkeys(
                        [
                            *assessment.reasons,
                            *(r.value for r in result.review.reasons),
                            *(r.value for d in assessment.fields for r in d.reasons),
                            *(f"table_{r}" for t in assessment.tables for r in t.reasons),
                        ]
                    )
                ),
                assessment=assessment,
                created_at=now,
                updated_at=now,
            )
            if auto:
                item.decision = Decision(
                    action="auto_accepted",
                    actor=SYSTEM,
                    at=now,
                    reason=f"calibration {assessment.calibration_id}",
                )
            events = [
                self._event(
                    item,
                    HistoryKind.REQUEUED if existing else HistoryKind.ENQUEUED,
                    SYSTEM,
                    details={
                        "previous_status": existing.status.value if existing else None,
                        "corrections_dropped": len(existing.corrections) if existing else 0,
                        "calibration": assessment.calibration_id,
                        "eligible": assessment.eligible,
                    },
                )
            ]
            if auto:
                events.append(self._event(item, HistoryKind.AUTO_ACCEPTED, SYSTEM))
            for event in events:
                self.reviews.append(event)
            if existing is not None:
                self.reviews.archive(existing)
            self.reviews.write(item)
        self.audit.record(
            AuditEvent.REVIEW_AUTO_ACCEPTED if auto else AuditEvent.REVIEW_QUEUED,
            document_id=item.document_id,
            layout_run=item.layout_run,
            revision=item.revision,
            calibration=assessment.calibration_id,
            fields_to_review=sum(1 for d in assessment.fields if not d.verified),
            priority=item.priority,
        )
        return item

    def queue(
        self,
        statuses: set[ReviewItemStatus],
        schema_id: str | None = None,
        reason: str | None = None,
        assigned_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[ReviewItemSummary]]:
        total, page = self.reviews.query(statuses, schema_id, reason, assigned_to, limit, offset)
        return total, [
            ReviewItemSummary(
                document_id=i.document_id,
                layout_run=i.layout_run,
                schema_id=i.schema_id,
                status=i.status,
                priority=i.priority,
                reasons=i.reasons,
                fields_to_review=sum(1 for d in i.assessment.fields if not d.verified),
                assigned_to=i.assigned_to,
                created_at=i.created_at,
                updated_at=i.updated_at,
            )
            for i in page
        ]

    def item(self, document_id: str, layout_run: str) -> ReviewItem:
        return self.reviews.read(document_id, layout_run)

    def _result(self, item: ReviewItem) -> ExtractionResult:
        result = self.store.read_extraction(item.document_id, item.layout_run)
        if result.created_at != item.extraction_created_at:
            raise AppError(
                ErrorCode.REVIEW_CONFLICT,
                "The document was extracted again; the review item is out of date",
            )
        return result

    def result(
        self, document_id: str, layout_run: str, today: date | None = None
    ) -> ReviewedResult:
        item = self.reviews.read(document_id, layout_run)
        schema = self.registry.get(item.schema_id or "")
        return reviewed(self._result(item), item, schema, today or date.today())

    def history(self, document_id: str, layout_run: str) -> list[HistoryEvent]:
        self.reviews.read(document_id, layout_run)
        return list(self.reviews.history(document_id, layout_run))

    # ------------------------------------------------------------ changes

    def _change(
        self,
        document_id: str,
        layout_run: str,
        request: VersionedRequest,
        apply: Callable[[ReviewItem], list[dict[str, Any]]],
    ) -> ReviewItem:
        with self.reviews.locked():
            item = self.reviews.read(document_id, layout_run)
            if item.version != request.expected_version:
                raise AppError(
                    ErrorCode.REVIEW_CONFLICT,
                    f"The item changed (version {item.version}); reload it and try again",
                )
            events = apply(item)
            item.version += 1
            item.updated_at = _now()
            # History first: no change is ever stored without its record.
            for event in events:
                record = self.reviews.append({**event, "version": item.version})
                if record.location in item.corrections:
                    item.corrections[record.location].seq = record.seq
            self.reviews.write(item)
        return item

    @staticmethod
    def _open(item: ReviewItem) -> None:
        if item.status not in OPEN:
            raise AppError(ErrorCode.REVIEW_CONFLICT, f"The item is {item.status.value}")

    def claim(
        self, document_id: str, layout_run: str, who: Principal, request: ClaimRequest
    ) -> ReviewItem:
        def apply(item: ReviewItem) -> list[dict[str, Any]]:
            self._open(item)
            taken = item.assigned_to not in (None, who.user)
            if taken and not (request.force and who.role is Role.ADMIN):
                raise AppError(
                    ErrorCode.REVIEW_CONFLICT, f"The item is assigned to {item.assigned_to}"
                )
            previous = item.assigned_to
            item.assigned_to = who.user
            item.status = ReviewItemStatus.IN_REVIEW
            return [
                self._event(
                    item,
                    HistoryKind.CLAIMED,
                    who.user,
                    role=who.role,
                    details={"previous_assignee": previous},
                )
            ]

        item = self._change(document_id, layout_run, request, apply)
        self._audit(AuditEvent.REVIEW_CLAIMED, item, who)
        return item

    def release(
        self, document_id: str, layout_run: str, who: Principal, request: VersionedRequest
    ) -> ReviewItem:
        def apply(item: ReviewItem) -> list[dict[str, Any]]:
            self._open(item)
            if item.assigned_to != who.user and who.role is not Role.ADMIN:
                raise AppError(ErrorCode.REVIEW_CONFLICT, "The item is not assigned to you")
            item.assigned_to = None
            item.status = ReviewItemStatus.PENDING
            return [self._event(item, HistoryKind.RELEASED, who.user, role=who.role)]

        item = self._change(document_id, layout_run, request, apply)
        self._audit(AuditEvent.REVIEW_RELEASED, item, who)
        return item

    def correct(
        self, document_id: str, layout_run: str, who: Principal, request: CorrectionRequest
    ) -> ReviewItem:
        def apply(item: ReviewItem) -> list[dict[str, Any]]:
            self._open(item)
            if item.assigned_to != who.user:
                raise AppError(ErrorCode.REVIEW_CONFLICT, "Claim the item before correcting it")
            schema = self.registry.get(item.schema_id or "")
            if schema is None:
                raise AppError(
                    ErrorCode.INVALID_CORRECTION,
                    "No document type: re-run extraction with a document type, or reject",
                )
            result = self._result(item)
            decisions = {d.field: d for d in item.assessment.fields}
            events = []
            for change in request.changes:
                kind, pattern, extracted, status, raw, confidence, method = target(
                    change, result, schema
                )
                location = change.location
                previous = item.corrections.get(location)
                current = previous.value if previous else extracted
                value = new_value(change, kind, pattern, current, status, self.policy.options)
                reason = change.reason or CorrectionReason.CONFIRMED
                item.corrections[location] = AppliedCorrection(
                    location=location,
                    action=change.action,
                    value=value,
                    previous=current,
                    reason=reason,
                    actor=who.user,
                    at=_now(),
                    seq=0,  # set to the history record's number when stored
                )
                decision = decisions.get(change.field or "")
                events.append(
                    self._event(
                        item,
                        HistoryKind.CORRECTED,
                        who.user,
                        role=who.role,
                        location=location,
                        field_type=kind.value,
                        action=change.action,
                        reason=reason.value,
                        comment=change.comment,
                        extracted_value=extracted,
                        extracted_status=status.value if status else None,
                        extracted_raw=raw,
                        previous_value=current,
                        new_value=value,
                        input_text=mask_text(change.value) if change.value else None,
                        confidence=confidence,
                        method=method,
                        details={
                            "verified": decision.verified if decision else None,
                            "engine": decision.engine.status.value if decision else None,
                            "threshold": decision.threshold if decision else None,
                            "reasons": [r.value for r in decision.reasons] if decision else [],
                        },
                    )
                )
            return events

        item = self._change(document_id, layout_run, request, apply)
        self._audit(AuditEvent.REVIEW_CORRECTED, item, who, changes=len(request.changes))
        return item

    def approve(
        self,
        document_id: str,
        layout_run: str,
        who: Principal,
        request: ApproveRequest,
        today: date | None = None,
    ) -> ReviewItem:
        def apply(item: ReviewItem) -> list[dict[str, Any]]:
            self._open(item)
            schema = self.registry.get(item.schema_id or "")
            if schema is None:
                raise AppError(
                    ErrorCode.REVIEW_INCOMPLETE, "Unclassified documents cannot be approved"
                )
            pending = unresolved(item)
            if pending:
                raise AppError(
                    ErrorCode.REVIEW_INCOMPLETE,
                    "Decide on these first: " + ", ".join(pending[:20]),
                )
            if self.require_second_person:
                authors = {c.actor for c in item.corrections.values()}
                if who.user in authors:
                    raise AppError(
                        ErrorCode.FORBIDDEN,
                        "Four-eyes rule: you corrected or confirmed values on this item",
                    )
            outcome = reviewed(self._result(item), item, schema, today or date.today())
            if not outcome.validation.valid:
                raise AppError(
                    ErrorCode.REVIEW_INCOMPLETE,
                    "The corrected record is not valid: "
                    + "; ".join(
                        ".".join(map(str, i.location)) for i in outcome.validation.issues[:10]
                    ),
                )
            failing = sorted(c.id for c in outcome.checks if c.status.value == "failed")
            unacknowledged = sorted(set(failing) - set(request.acknowledge_failed_checks))
            if unacknowledged:
                raise AppError(
                    ErrorCode.REVIEW_INCOMPLETE,
                    "Checks still fail: " + ", ".join(unacknowledged),
                )
            if failing and not (request.comment or "").strip():
                raise AppError(
                    ErrorCode.REVIEW_INCOMPLETE, "Approving with failing checks needs a comment"
                )
            item.status = ReviewItemStatus.APPROVED
            item.assigned_to = None
            item.decision = Decision(
                action="approved",
                actor=who.user,
                role=who.role.value,
                at=_now(),
                comment=request.comment,
                acknowledged_checks=failing,
            )
            return [
                self._event(
                    item,
                    HistoryKind.APPROVED,
                    who.user,
                    role=who.role,
                    comment=request.comment,
                    details={
                        "acknowledged_checks": failing,
                        "corrections": len(item.corrections),
                    },
                )
            ]

        item = self._change(document_id, layout_run, request, apply)
        self._audit(AuditEvent.REVIEW_APPROVED, item, who)
        return item

    def reject(
        self, document_id: str, layout_run: str, who: Principal, request: RejectRequest
    ) -> ReviewItem:
        def apply(item: ReviewItem) -> list[dict[str, Any]]:
            self._open(item)
            item.status = ReviewItemStatus.REJECTED
            item.assigned_to = None
            item.decision = Decision(
                action="rejected",
                actor=who.user,
                role=who.role.value,
                at=_now(),
                reason=request.reason.value,
                comment=request.comment,
            )
            return [
                self._event(
                    item,
                    HistoryKind.REJECTED,
                    who.user,
                    role=who.role,
                    reason=request.reason.value,
                    comment=request.comment,
                )
            ]

        item = self._change(document_id, layout_run, request, apply)
        self._audit(AuditEvent.REVIEW_REJECTED, item, who, reason=request.reason.value)
        return item

    def _audit(self, event: AuditEvent, item: ReviewItem, who: Principal, **fields: Any) -> None:
        self.audit.record(
            event,
            actor=who.user,
            role=who.role.value,
            document_id=item.document_id,
            layout_run=item.layout_run,
            revision=item.revision,
            version=item.version,
            **fields,
        )

    # ------------------------------------------------------------ source images

    def _evidence(
        self,
        result: ExtractionResult,
        field: str | None,
        table: str | None,
        record: int | None,
        column: str | None,
        candidate: int | None,
    ) -> tuple[list[Evidence], list[Evidence]]:
        """(evidence of the value, evidence of the other candidates)."""
        if field is not None:
            found = next((f for f in result.fields if f.name == field), None)
            if found is None:
                raise AppError(ErrorCode.EVIDENCE_NOT_FOUND, "No such field")
            others = [e for c in found.candidates for e in c.evidence]
            if candidate is not None:
                if not 0 <= candidate < len(found.candidates):
                    raise AppError(ErrorCode.EVIDENCE_NOT_FOUND, "No such candidate")
                return list(found.candidates[candidate].evidence), others
            return list(found.evidence) or others, others
        t = next((t for t in result.tables if t.name == table), None)
        r = next((r for r in t.records if r.index == record), None) if t else None
        cell = r.values.get(column or "") if r else None
        if cell is None or cell.evidence is None:
            raise AppError(ErrorCode.EVIDENCE_NOT_FOUND, "No evidence for this cell")
        return [cell.evidence], []

    def _page_image(
        self, result: ExtractionResult, page_number: int, units: str
    ) -> tuple[Any, float, list[BoundingBox]]:
        """Page image, scale from evidence units to its pixels, and Aadhaar redactions."""
        schema = self.registry.get(result.schema_id or "")
        mask = self.policy.options.mask_aadhaar and (
            schema is None or any(f.type is FieldType.AADHAAR for f in schema.fields)
        )
        if units == "pt":
            path = self.store.page_image_path(result.document_id, page_number)
            with Image.open(path) as img:
                image = np.asarray(img.convert("L"))
            text = self.store.read_page_text(result.document_id, page_number)
            scale = image.shape[1] / text.page_width if text.page_width else 1.0
            lines = [(s.text, s.bbox) for s in text.segments]
            return image, scale, crops.aadhaar_redactions(lines) if mask else []
        ocr = self.store.read_ocr_run(result.document_id, result.ocr_run)
        page = next((p for p in ocr.pages if p.page_number == page_number), None)
        if page is None or page.image_width is None:
            raise AppError(ErrorCode.PAGE_IMAGE_NOT_AVAILABLE, "No OCR image for this page")
        image = ocr_page_image(
            self.store,
            result.document_id,
            page_number,
            page.image_source,
            page.image_rotation_deg,
            (page.image_width, page.image_height),
        )
        lines = [(ln.text, ln.bbox) for ln in page.lines]
        return image, 1.0, crops.aadhaar_redactions(lines) if mask else []

    def crop(
        self,
        document_id: str,
        layout_run: str,
        field: str | None = None,
        table: str | None = None,
        record: int | None = None,
        column: str | None = None,
        candidate: int | None = None,
    ) -> bytes:
        item = self.reviews.read(document_id, layout_run)
        result = self._result(item)
        evidence, _ = self._evidence(result, field, table, record, column, candidate)
        if not evidence:
            raise AppError(ErrorCode.EVIDENCE_NOT_FOUND, "Nothing was found on the page")
        page = evidence[0].page_number
        on_page = [e for e in evidence if e.page_number == page]
        image, scale, redact = self._page_image(result, page, on_page[0].units)
        return crops.crop(image, [e.bbox for e in on_page], redact, scale)

    def page(
        self, document_id: str, layout_run: str, page_number: int, field: str | None = None
    ) -> bytes:
        item = self.reviews.read(document_id, layout_run)
        result = self._result(item)
        boxes: list[Evidence] = []
        others: list[Evidence] = []
        if field is not None:
            boxes, others = self._evidence(result, field, None, None, None, None)
        boxes = [e for e in boxes if e.page_number == page_number]
        others = [e for e in others if e.page_number == page_number and e not in boxes]
        units = (
            (boxes or others)[0].units if (boxes or others) else self._units(result, page_number)
        )
        image, scale, redact = self._page_image(result, page_number, units)
        return crops.page_preview(
            image, [e.bbox for e in boxes], [e.bbox for e in others], redact, scale
        )

    def _units(self, result: ExtractionResult, page_number: int) -> str:
        ocr = self.store.read_ocr_run(result.document_id, result.ocr_run)
        page = next((p for p in ocr.pages if p.page_number == page_number), None)
        if page is None:
            raise AppError(ErrorCode.PAGE_NOT_FOUND, "Page not found")
        return "px" if page.image_width else "pt"

    # ------------------------------------------------------------ patterns and integrity

    def verify(self) -> ChainVerification:
        return self.reviews.verify()

    def patterns(self) -> CorrectionPatterns:
        events = list(self.reviews.history())
        decided = Counter(i.status.value for i in self.reviews.items())
        corrections = [e for e in events if e.kind is HistoryKind.CORRECTED and e.location]
        fields: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        by_type: dict[str, Counter[str]] = defaultdict(Counter)
        confusions: Counter[tuple[str, str]] = Counter()
        verified_wrong = []
        for e in corrections:
            key = (e.schema_id or "-", e.location or "-")
            changed = e.action != "confirm"
            fields[key]["reviewed"] += 1
            fields[key]["changed"] += changed
            fields[key][f"reason:{e.reason}"] += 1
            by_type[e.field_type or "-"][e.reason or "-"] += 1
            if changed and e.details.get("verified"):
                verified_wrong.append(
                    {
                        "schema_id": e.schema_id,
                        "location": e.location,
                        "reason": e.reason,
                        "confidence": e.confidence,
                        "threshold": e.details.get("threshold"),
                        "seq": e.seq,
                    }
                )
            if e.reason == CorrectionReason.OCR_MISREAD.value:
                before, after = (
                    str(e.extracted_value or e.extracted_raw or ""),
                    str(e.new_value or ""),
                )
                matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
                for op, a0, a1, b0, b1 in matcher.get_opcodes():
                    if op == "replace" and a1 - a0 <= 3 and b1 - b0 <= 3:
                        confusions[(before[a0:a1], after[b0:b1])] += 1
        rows: list[dict[str, str | int | float | None]] = []
        for (schema_id, location), counts in sorted(fields.items()):
            reasons = {k[7:]: v for k, v in counts.items() if k.startswith("reason:")}
            rows.append(
                {
                    "schema_id": schema_id,
                    "location": location,
                    "reviewed": counts["reviewed"],
                    "changed": counts["changed"],
                    "change_rate": round(counts["changed"] / counts["reviewed"], 4),
                    "top_reason": max(reasons, key=lambda r: reasons[r]) if reasons else None,
                }
            )
        rows.sort(key=lambda r: (-int(r["changed"] or 0), str(r["location"])))
        return CorrectionPatterns(
            events=len(events),
            items_decided=dict(decided),
            by_field=rows,
            by_reason=dict(Counter(e.reason or "-" for e in corrections)),
            by_type={k: dict(v) for k, v in sorted(by_type.items())},
            character_confusions=[
                {"read": a, "correct": b, "count": n} for (a, b), n in confusions.most_common(30)
            ],
            verified_but_corrected=verified_wrong,
        )
