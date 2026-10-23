"""Verification, review queue and correction history schemas (ADR-0028; docs/REVIEW.md).

A review item is created for every extraction result. Each field and table carries a
decision: ``verified`` only when it passes every gate (calibrated confidence threshold,
engine agreement, business-rule checks, clean source page); otherwise it lists why it needs
a person. Corrections and decisions are appended to a hash-chained history.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from newgen_ocr.auth.tokens import Role
from newgen_ocr.extraction.models import CheckResult, FieldStatus, ValidationReport, Value

NAME = r"^[a-z][a-z0-9_]{0,47}$"


# ------------------------------------------------------------ verification


class EngineAgreement(StrEnum):
    AGREE = "agree"  # the second engine read the same normalised value in the same place
    DISAGREE = "disagree"  # it read text there, but not this value
    NO_READING = "no_reading"  # it read nothing there (recorded; not a disagreement)
    UNAVAILABLE = "unavailable"  # no second run, a different page image, or no OCR evidence


class EngineCheck(BaseModel):
    status: EngineAgreement
    engine: str | None = None
    readings: list[str] = []  # the second engine's typed readings of that area (masked)
    text: str | None = None  # the second engine's text there (masked)


class FieldReviewReason(StrEnum):
    REQUIRED_MISSING = "required_missing"
    MISSING_UNVERIFIED = "missing_unverified"  # optional and absent: absence not calibrated
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"
    INVALID = "invalid"
    NOT_CALIBRATED = "not_calibrated"  # no threshold supported by evaluation data
    BELOW_THRESHOLD = "below_threshold"
    NO_CONFIDENCE = "no_confidence"  # evidence without an OCR confidence (e.g. PDF text)
    ENGINE_DISAGREEMENT = "engine_disagreement"
    CHECK_FAILED = "check_failed"  # a business rule involving the value failed
    SOURCE_FLAGGED = "source_flagged"  # OCR or layout routed the evidence page to review
    DOCUMENT_TYPE = "document_type"  # classification not reliable: nothing is verified


class FieldDecision(BaseModel):
    field: str
    type: str
    required: bool
    status: FieldStatus
    verified: bool
    reasons: list[FieldReviewReason] = []
    details: list[str] = []
    confidence: float | None = None
    threshold: float | None = None
    calibration_key: str | None = None  # the calibration entry used
    engine: EngineCheck = EngineCheck(status=EngineAgreement.UNAVAILABLE)
    checks_passed: list[str] = []
    checks_failed: list[str] = []


class TableDecision(BaseModel):
    table: str
    status: str
    verified: bool
    reasons: list[str] = []
    checks_passed: list[str] = []
    checks_failed: list[str] = []
    cells_flagged: list[str] = []  # "<record>.<column>" of invalid/ambiguous/disagreeing cells
    engine_disagreements: int = 0
    # Disagreeing cells in columns a passing row-level check covers (arithmetic confirms them).
    engine_disagreements_corroborated: int = 0


class Assessment(BaseModel):
    calibration_id: str | None
    auto_accept_enabled: bool
    # Every field and table verified and no document-level reason: acceptable without a person.
    eligible: bool
    reasons: list[str] = []  # document-level
    fields: list[FieldDecision] = []
    tables: list[TableDecision] = []


# ------------------------------------------------------------ calibration


class CalibrationKind(StrEnum):
    VALUE = "value"  # an extracted value is correct
    MISSING = "missing"  # an optional field reported missing is really absent


class CalibrationStatus(StrEnum):
    CALIBRATED = "calibrated"
    INSUFFICIENT_DATA = "insufficient_data"
    TARGET_NOT_MET = "target_not_met"


class ReliabilityBin(BaseModel):
    low: float
    high: float
    n: int
    correct: int


class CalibrationEntry(BaseModel):
    key: str  # "value:gst_invoice.grand_total", "value:type:amount", "missing:receipt.total"
    kind: CalibrationKind
    level: Literal["field", "type"]
    status: CalibrationStatus
    threshold: float | None = None  # lowest confidence accepted (value entries)
    observations: int  # gated observations (passing every non-confidence gate)
    accepted: int = 0  # observations at or above the threshold
    correct: int = 0  # ... of which correct
    precision: float | None = None
    lower_bound: float | None = None  # one-sided Wilson lower bound of the precision
    coverage: float | None = None  # accepted / observations
    errors_above_threshold: int = 0
    reliability: list[ReliabilityBin] = []


class CalibrationDataset(BaseModel):
    name: str
    kind: Literal["synthetic", "real", "reviews"]
    samples: int
    description: str = ""
    manifest_sha256: str | None = None


class SplitEvaluation(BaseModel):
    """Thresholds applied to a held-out test split, never used to choose them."""

    samples: int
    documents_eligible: int
    documents_eligible_correct: int  # eligible documents whose every value was right
    values_verified: int
    values_verified_correct: int
    values_total: int
    missing_verified: int
    missing_verified_correct: int


class Calibration(BaseModel):
    id: str
    created_at: datetime
    pipeline_version: str
    dataset: CalibrationDataset
    target_precision: float
    confidence_level: float
    min_samples: int
    schema_versions: dict[str, str]
    entries: dict[str, CalibrationEntry]
    test: SplitEvaluation | None = None
    notes: list[str] = []


# ------------------------------------------------------------ review items


class ReviewItemStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"  # claimed by a reviewer
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_ACCEPTED = "auto_accepted"


OPEN = (ReviewItemStatus.PENDING, ReviewItemStatus.IN_REVIEW)


class CorrectionReason(StrEnum):
    CONFIRMED = "confirmed"  # the extracted value is right
    OCR_MISREAD = "ocr_misread"
    WRONG_VALUE_SELECTED = "wrong_value_selected"  # another text on the page was taken
    MISSED_VALUE = "missed_value"  # on the page, but not extracted
    VALUE_NOT_PRESENT = "value_not_present"  # extracted, but not on the page
    AMBIGUITY_RESOLVED = "ambiguity_resolved"
    CONFLICT_RESOLVED = "conflict_resolved"
    NORMALISATION_ERROR = "normalisation_error"  # read right, converted wrongly
    OTHER = "other"  # comment required


class RejectReason(StrEnum):
    ILLEGIBLE = "illegible"
    WRONG_DOCUMENT_TYPE = "wrong_document_type"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    INCOMPLETE_DOCUMENT = "incomplete_document"
    DUPLICATE = "duplicate"
    OTHER = "other"  # comment required


class AppliedCorrection(BaseModel):
    location: str  # "fields.<name>", "tables.<table>.<record>.<column>" or "tables.<table>"
    action: Literal["set", "confirm", "clear"]
    value: Value = None
    previous: Value = None
    reason: CorrectionReason
    actor: str
    at: datetime
    seq: int  # history sequence number


class Decision(BaseModel):
    action: Literal["approved", "rejected", "auto_accepted"]
    actor: str
    role: str | None = None
    at: datetime
    reason: str | None = None
    comment: str | None = None
    acknowledged_checks: list[str] = []


class ReviewItem(BaseModel):
    document_id: str
    layout_run: str
    schema_id: str | None
    schema_version: str | None
    extraction_created_at: datetime
    revision: int = 1  # increases when the document is extracted again
    version: int = 1  # increases on every change (optimistic concurrency)
    status: ReviewItemStatus
    priority: int
    reasons: list[str]
    assessment: Assessment
    assigned_to: str | None = None
    corrections: dict[str, AppliedCorrection] = {}  # by location; the latest per location
    decision: Decision | None = None
    created_at: datetime
    updated_at: datetime


class ReviewItemSummary(BaseModel):
    document_id: str
    layout_run: str
    schema_id: str | None
    status: ReviewItemStatus
    priority: int
    reasons: list[str]
    fields_to_review: int
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------ requests


class Change(BaseModel):
    field: str | None = Field(default=None, pattern=NAME)
    table: str | None = Field(default=None, pattern=NAME)
    record: int | None = Field(default=None, ge=0)
    column: str | None = Field(default=None, pattern=NAME)
    action: Literal["set", "confirm", "clear"] = "set"
    value: str | None = Field(default=None, max_length=2000)  # text as on the page
    reason: CorrectionReason | None = None
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _shape(self) -> Change:
        if (self.field is None) == (self.table is None):
            raise ValueError("give either field or table")
        if self.table is not None and (self.record is None) != (self.column is None):
            raise ValueError("a cell needs both record and column")
        if self.table is not None and self.record is None and self.action != "confirm":
            raise ValueError("a whole table can only be confirmed")
        if self.action == "set" and not (self.value or "").strip():
            raise ValueError("set needs a value; use clear for a value not on the page")
        if self.action != "set" and self.value is not None:
            raise ValueError(f"{self.action} takes no value")
        if self.action == "confirm" and self.reason not in (None, CorrectionReason.CONFIRMED):
            raise ValueError("confirm has reason 'confirmed'")
        if self.action != "confirm" and self.reason is None:
            raise ValueError("a reason code is required")
        if self.reason is CorrectionReason.OTHER and not (self.comment or "").strip():
            raise ValueError("reason 'other' needs a comment")
        return self

    @property
    def location(self) -> str:
        if self.field is not None:
            return f"fields.{self.field}"
        if self.record is None:
            return f"tables.{self.table}"
        return f"tables.{self.table}.{self.record}.{self.column}"


class VersionedRequest(BaseModel):
    expected_version: int


class CorrectionRequest(VersionedRequest):
    changes: list[Change] = Field(min_length=1, max_length=200)


class ClaimRequest(VersionedRequest):
    force: bool = False  # admin only: take over another reviewer's item


class ApproveRequest(VersionedRequest):
    comment: str | None = Field(default=None, max_length=1000)
    # Checks that still fail after corrections and are accepted knowingly (comment required).
    acknowledge_failed_checks: list[str] = []


class RejectRequest(VersionedRequest):
    reason: RejectReason
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _comment(self) -> RejectRequest:
        if self.reason is RejectReason.OTHER and not (self.comment or "").strip():
            raise ValueError("reason 'other' needs a comment")
        return self


# ------------------------------------------------------------ results and history


class ReviewedValue(BaseModel):
    value: Value
    status: FieldStatus
    source: Literal["extracted", "verified", "confirmed", "corrected", "cleared", "unresolved"]
    corrected_by: str | None = None


class ReviewedResult(BaseModel):
    """The extraction with corrections applied, re-validated and re-checked."""

    document_id: str
    layout_run: str
    schema_id: str | None
    status: ReviewItemStatus
    revision: int
    record: dict[str, Value]
    fields: dict[str, ReviewedValue]
    tables: dict[str, list[dict[str, Value]]]
    validation: ValidationReport
    checks: list[CheckResult]
    unresolved: list[str]  # locations that still need a decision before approval
    decision: Decision | None = None


class HistoryKind(StrEnum):
    ENQUEUED = "enqueued"
    REQUEUED = "requeued"  # extracted again: a new revision
    AUTO_ACCEPTED = "auto_accepted"
    CLAIMED = "claimed"
    RELEASED = "released"
    CORRECTED = "corrected"
    APPROVED = "approved"
    REJECTED = "rejected"


class HistoryEvent(BaseModel):
    seq: int
    ts: datetime
    kind: HistoryKind
    actor: str
    role: Role | None = None
    request_id: str | None = None
    document_id: str
    layout_run: str
    schema_id: str | None = None
    revision: int
    version: int  # item version after the event
    # Corrections only. Values of sensitive fields are masked.
    location: str | None = None
    field_type: str | None = None
    action: str | None = None
    reason: str | None = None
    comment: str | None = None
    extracted_value: Value = None
    extracted_status: str | None = None
    extracted_raw: str | None = None
    previous_value: Value = None
    new_value: Value = None
    input_text: str | None = None
    confidence: float | None = None
    method: str | None = None
    details: dict[str, str | int | float | bool | list[str] | None] = {}
    prev_hash: str
    hash: str


class ChainVerification(BaseModel):
    valid: bool
    events: int
    first_bad_seq: int | None = None
    message: str


class CorrectionPatterns(BaseModel):
    """Aggregates over the correction history, for improving rules and thresholds."""

    events: int
    items_decided: dict[str, int]
    by_field: list[dict[str, str | int | float | None]]
    by_reason: dict[str, int]
    by_type: dict[str, dict[str, int]]
    character_confusions: list[dict[str, str | int]]
    verified_but_corrected: list[dict[str, str | int | float | None]]
