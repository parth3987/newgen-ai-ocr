"""Run OCR over a stored document's pages (ADR-0022, ADR-0023).

Each page is first *planned*: image choice (preprocessed or source), script and
orientation detection, rotation, the languages to read and any pre-OCR review reasons.
Plans are then executed by the selected engine(s), so primary, fallback and comparison
engines all read exactly the same images.

Page policy:
- native-text PDF pages and blank pages are not OCRed;
- ``languages=["auto"]`` picks languages per page from the detected script; an unsupported,
  uncertain or undetectable script is never guessed: the page is ``unsupported`` and routed
  to review;
- ``engine="auto"`` uses the per-language primary engine, falling back per page only when it
  is unavailable or fails; a named engine never falls back; engines are used only for
  languages they are enabled for in the language matrix;
- ``handwriting=True`` uses the handwriting engine where the language has one, otherwise the
  page is ``unsupported``; handwriting results are routed to review unless the language is
  verified on real samples;
- ``compare=True`` also runs the secondary engine and records agreement; results are never
  merged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from PIL import Image
from pydantic import BaseModel

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.quality import QualityFlag
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.ingestion.models import DocumentManifest, PageManifest
from newgen_ocr.ocr.compare import compare_runs
from newgen_ocr.ocr.engine import OcrEngine, page_orientation
from newgen_ocr.ocr.language_id import identify
from newgen_ocr.ocr.languages import LANGUAGES, SCRIPT_LANGUAGES, HandwritingStatus
from newgen_ocr.ocr.models import (
    SUPPORTED_LANGUAGES,
    DetectionStatus,
    EngineInfo,
    OcrComparison,
    OcrPage,
    OcrRun,
    PageOcrStatus,
    PageReview,
    ReviewReason,
    ScriptDetection,
)
from newgen_ocr.ocr.script_detect import ScriptDetector, rotate_clockwise
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

AUTO = "auto"
HANDWRITING_ENGINE = "paddle_handwriting"
LOW_QUALITY_FLAGS = frozenset({QualityFlag.BLURRY, QualityFlag.LOW_RESOLUTION})
_ROTATIONS = frozenset({90, 180, 270})


class EngineStatus(BaseModel):
    name: str
    role: str  # "primary" | "fallback" | "handwriting" | "available"
    available: bool
    reason: str | None
    languages: list[str]


@dataclass(frozen=True)
class ReviewPolicy:
    min_page_confidence: float
    min_line_confidence: float
    handwriting_verified: frozenset[str] = frozenset()


@dataclass
class PagePlan:
    page: PageManifest
    skip: str | None = None
    image: np.ndarray | None = None
    source: str | None = None
    rotation: int = 0
    detection: ScriptDetection | None = None
    languages: list[str] = field(default_factory=list)
    auto: bool = False  # languages were chosen by script identification
    # Reasons that make the page unrecognisable (it will not be OCRed).
    blockers: list[tuple[ReviewReason, str]] = field(default_factory=list)
    # Reasons for review that do not prevent OCR.
    review: list[tuple[ReviewReason, str]] = field(default_factory=list)


def route_languages(script: str, secondary: list[str]) -> tuple[list[str], str]:
    """Languages to read for a page's dominant (+ secondary) scripts.

    An Indic recogniser also reads Latin, but the English recogniser cannot read Indic text,
    so a page with an Indic secondary script is read with the Indic recogniser first.
    Returns (languages, note); a non-empty note means the mix cannot be fully covered by one
    PaddleOCR model (two different Indic scripts) and the page should be reviewed.
    """
    scripts = [script, *secondary]
    indic = [s for s in scripts if s != "Latin"]
    languages: list[str] = []
    for s in indic:
        languages += [
            lang for lang in SCRIPT_LANGUAGES[s] if lang != "en" and lang not in languages
        ]
    languages.append("en")  # Indic recognisers read Latin; English always comes last
    note = f"multiple Indic scripts: {', '.join(indic)}" if len(indic) > 1 else ""
    return languages, note


def enabled_for(engine: str, language: str) -> bool:
    """Whether the language matrix enables this engine for this language."""
    info = LANGUAGES.get(language)
    if engine == HANDWRITING_ENGINE:
        return info is not None and info.handwriting is not HandwritingStatus.UNSUPPORTED
    return info is not None and engine in info.printed_engines


class OcrService:
    def __init__(
        self,
        store: DocumentStore,
        audit: AuditLog,
        engines: dict[str, OcrEngine],
        primary: str,
        fallback: str | None,
        disagreement_threshold: float,
        language_primary: dict[str, str] | None = None,
        detector: ScriptDetector | None = None,
        detect_orientation: bool = True,
        review_policy: ReviewPolicy | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.engines = engines
        self.primary = primary
        self.fallback = fallback
        self.threshold = disagreement_threshold
        self.language_primary = dict(language_primary or {})
        self.detector = detector
        self.detect_orientation = detect_orientation
        self.policy = review_policy or ReviewPolicy(
            min_page_confidence=0.0, min_line_confidence=0.0
        )

    # ------------------------------------------------------------ engines

    def _roles(self, languages: Sequence[str]) -> tuple[str, str | None]:
        """(primary, fallback) for a request; per-language overrides swap the engines."""
        primary = self.language_primary.get(languages[0], self.primary)
        if primary == self.primary:
            return primary, self.fallback
        return primary, self.primary if self.fallback is not None else None

    def status(self) -> list[EngineStatus]:
        result = []
        for name, engine in self.engines.items():
            reason = engine.unavailable_reason()
            role = "available"
            if name == self.primary:
                role = "primary"
            elif name == self.fallback:
                role = "fallback"
            elif name == HANDWRITING_ENGINE:
                role = "handwriting"
            result.append(
                EngineStatus(
                    name=name,
                    role=role,
                    available=reason is None,
                    reason=reason,
                    languages=sorted(
                        lang for lang in engine.languages() if enabled_for(name, lang)
                    ),
                )
            )
        return result

    def _engine(self, name: str) -> OcrEngine:
        if name not in self.engines or name == HANDWRITING_ENGINE:
            raise AppError(ErrorCode.UNKNOWN_ENGINE, f"Unknown OCR engine: {name[:40]!r}")
        return self.engines[name]

    def _usable(self, name: str | None, languages: Sequence[str]) -> OcrEngine | None:
        if name is None or name not in self.engines or not languages:
            return None
        engine = self.engines[name]
        if engine.unavailable_reason() is not None:
            return None
        if languages[0] not in engine.languages() or not enabled_for(name, languages[0]):
            return None
        return engine

    @staticmethod
    def _check_languages(languages: Sequence[str]) -> bool:
        """Validate the request languages; returns True for automatic detection."""
        if list(languages) == [AUTO]:
            return True
        if not languages:
            raise AppError(ErrorCode.UNSUPPORTED_LANGUAGE, "At least one language is required")
        unknown = [lang for lang in languages if lang not in SUPPORTED_LANGUAGES]
        if unknown:
            raise AppError(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                f"Unsupported language(s): {', '.join(unknown)[:60]}; "
                f"supported: {', '.join(SUPPORTED_LANGUAGES)} or 'auto'",
            )
        return False

    # ------------------------------------------------------------ planning

    def _image(
        self, document_id: str, page: PageManifest, profile: str | None
    ) -> tuple[np.ndarray, str]:
        if profile is not None and profile in self.store.list_runs(document_id):
            run = self.store.read_run(document_id, profile)
            if run.pages[page.page_number - 1].output is not None:
                path = self.store.preprocessed_image_path(document_id, profile, page.page_number)
                with Image.open(path) as img:
                    return np.asarray(img), f"preprocessed:{profile}"
        path = self.store.page_image_path(document_id, page.page_number)
        with Image.open(path) as img:
            return np.asarray(img), "source"

    @staticmethod
    def _skip(page: PageManifest) -> str | None:
        if not page.needs_ocr:
            return "native_text"
        if page.image is None:
            return "no_page_image"
        if page.quality is not None and QualityFlag.BLANK_PAGE in page.quality.flags:
            return "blank_page"
        return None

    def _plan(
        self,
        document_id: str,
        page: PageManifest,
        profile: str | None,
        languages: Sequence[str],
        auto: bool,
    ) -> PagePlan:
        skip = self._skip(page)
        if skip is not None:
            return PagePlan(page=page, skip=skip)
        image, source = self._image(document_id, page, profile)
        plan = PagePlan(page=page, image=image, source=source, languages=list(languages), auto=auto)
        if page.quality is not None:
            low = sorted(f.value for f in page.quality.flags if f in LOW_QUALITY_FLAGS)
            if low:
                plan.review.append((ReviewReason.LOW_QUALITY, ",".join(low)))

        detection = None
        if self.detector is not None and (auto or self.detect_orientation):
            # Explicit languages: orientation only. Auto: orientation + script census.
            detection = self.detector.detect(image, identify_script=auto)
            plan.detection = detection
            if detection.orientation_reliable and detection.orientation_deg in _ROTATIONS:
                plan.rotation = int(detection.orientation_deg or 0)
                plan.image = rotate_clockwise(image, plan.rotation)

        if auto:
            plan.languages = []
            if detection is None or detection.status in (
                DetectionStatus.UNAVAILABLE,
                DetectionStatus.UNDETERMINED,
            ):
                plan.blockers.append((ReviewReason.SCRIPT_UNCERTAIN, "script not detectable"))
            elif detection.status is DetectionStatus.UNCERTAIN:
                plan.blockers.append((ReviewReason.SCRIPT_UNCERTAIN, f"census {detection.census}"))
            elif detection.script not in SCRIPT_LANGUAGES:
                plan.blockers.append((ReviewReason.UNSUPPORTED_SCRIPT, str(detection.script)))
            else:
                plan.languages, mixed = route_languages(
                    str(detection.script), detection.secondary_scripts
                )
                if mixed:
                    plan.review.append((ReviewReason.MIXED_SCRIPT, mixed))
        return plan

    # ------------------------------------------------------------ execution

    def _ocr_page(self, plan: PagePlan, engine: OcrEngine) -> OcrPage:
        if plan.image is None:  # planning only executes pages that have an image
            raise AppError(ErrorCode.INTERNAL_ERROR, "Page has no image to recognise")
        start = time.perf_counter()
        lines = engine.recognize(plan.image, plan.languages)
        confidences = [line.confidence for line in lines if line.confidence is not None]
        return OcrPage(
            page_number=plan.page.page_number,
            status=PageOcrStatus.OK if lines else PageOcrStatus.EMPTY,
            engine=engine.name,
            image_source=plan.source,
            image_width=int(plan.image.shape[1]),
            image_height=int(plan.image.shape[0]),
            orientation_deg=page_orientation(lines),
            lines=lines,
            text="\n".join(line.text for line in lines),
            mean_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def _execute(
        self,
        plan: PagePlan,
        engine_name: str,
        handwriting: bool,
        down_reason: str | None,
        used: dict[str, EngineInfo],
    ) -> OcrPage:
        number = plan.page.page_number
        if plan.skip is not None:
            return OcrPage(page_number=number, status=PageOcrStatus.SKIPPED, skip_reason=plan.skip)
        blockers = list(plan.blockers)
        primary: OcrEngine | None = None
        fallback: OcrEngine | None = None
        primary_name = engine_name
        if not blockers:
            if handwriting:
                if enabled_for(HANDWRITING_ENGINE, plan.languages[0]):
                    primary = self._usable(HANDWRITING_ENGINE, plan.languages)
                    primary_name = HANDWRITING_ENGINE
                if primary is None:
                    blockers.append(
                        (ReviewReason.HANDWRITING_UNSUPPORTED, f"no engine for {plan.languages[0]}")
                    )
            elif engine_name == AUTO:
                primary_name, fallback_name = self._roles(plan.languages)
                primary = self._usable(primary_name, plan.languages)
                fallback = self._usable(fallback_name, plan.languages)
                if primary is None and fallback is None:
                    blockers.append(
                        (ReviewReason.LANGUAGE_NOT_ENABLED, f"no engine for {plan.languages[0]}")
                    )
            else:
                primary = self._usable(engine_name, plan.languages)
                if primary is None:
                    blockers.append(
                        (
                            ReviewReason.LANGUAGE_NOT_ENABLED,
                            f"{engine_name} is not enabled for {plan.languages[0]}",
                        )
                    )
        if blockers:
            return OcrPage(
                page_number=number,
                status=PageOcrStatus.UNSUPPORTED,
                image_source=plan.source,
                languages=plan.languages,
                handwriting=handwriting,
                image_rotation_deg=plan.rotation,
                script=plan.detection,
                review=_review_from(blockers + plan.review),
            )

        result: OcrPage | None = None
        failure: AppError | None = None
        if primary is not None:
            try:
                result = self._ocr_page(plan, primary)
                used.setdefault(primary.name, primary.info(plan.languages))
            except AppError as exc:
                failure = exc
        if result is None and fallback is not None:
            reason = failure.code.value if failure else (down_reason or "primary_unavailable")
            try:
                result = self._ocr_page(plan, fallback)
                used.setdefault(fallback.name, fallback.info(plan.languages))
                result.fallback_from = primary_name
                result.fallback_reason = reason
            except AppError as exc:
                failure = exc
        if result is None:
            code = failure.code if failure else ErrorCode.OCR_ENGINE_UNAVAILABLE
            logger.warning("ocr page failed", extra={"error_code": code.value})
            result = OcrPage(
                page_number=number,
                status=PageOcrStatus.FAILED,
                error_code=code.value,
                image_source=plan.source,
            )
        result.languages = plan.languages
        result.handwriting = handwriting
        result.image_rotation_deg = plan.rotation
        result.script = plan.detection
        if result.text:
            result.detected_language = identify(result.text)
        result.review = self._review(plan, result, handwriting)
        return result

    def _review(self, plan: PagePlan, page: OcrPage, handwriting: bool) -> PageReview:
        reasons = list(plan.review)
        if page.status is PageOcrStatus.FAILED:
            reasons.append((ReviewReason.OCR_FAILED, page.error_code or ""))
        elif page.status is PageOcrStatus.EMPTY:
            reasons.append((ReviewReason.EMPTY_RESULT, "no text on a non-blank page"))
        else:
            if (
                page.mean_confidence is not None
                and page.mean_confidence < self.policy.min_page_confidence
            ):
                reasons.append((ReviewReason.LOW_CONFIDENCE, f"mean {page.mean_confidence}"))
            low = [
                line
                for line in page.lines
                if line.confidence is not None and line.confidence < self.policy.min_line_confidence
            ]
            if low:
                reasons.append(
                    (ReviewReason.LOW_CONFIDENCE_LINES, f"{len(low)} of {len(page.lines)} lines")
                )
            guess = page.detected_language
            if guess is not None and guess.scripts.get("Other", 0.0) >= 0.05:
                reasons.append((ReviewReason.UNSUPPORTED_SCRIPT, "unsupported characters in text"))
            if guess is not None and not plan.auto and plan.languages and guess.scripts:
                # Judged from the engine's own output: e.g. an English page read with a
                # Devanagari recogniser comes back mostly Latin.
                expected = LANGUAGES[plan.languages[0]].script
                if guess.scripts.get(expected, 0.0) < 0.4:
                    dominant = max(guess.scripts, key=lambda s: guess.scripts[s])
                    reasons.append(
                        (ReviewReason.SCRIPT_MISMATCH, f"expected {expected}, text is {dominant}")
                    )
        if (
            handwriting
            and plan.languages
            and plan.languages[0] not in self.policy.handwriting_verified
        ):
            reasons.append(
                (ReviewReason.HANDWRITING_UNVERIFIED, "not verified on real handwriting")
            )
        return _review_from(reasons)

    def _run_plans(
        self,
        manifest: DocumentManifest,
        plans: list[PagePlan],
        requested: str,
        engine_name: str,
        languages: Sequence[str],
        profile: str | None,
        handwriting: bool,
        down_reason: str | None = None,
    ) -> OcrRun:
        start = time.perf_counter()
        used: dict[str, EngineInfo] = {}
        pages = [self._execute(p, engine_name, handwriting, down_reason, used) for p in plans]
        detection_ms = sum(p.detection.duration_ms for p in plans if p.detection)
        return OcrRun(
            document_id=manifest.document_id,
            requested_engine=requested,
            engines=list(used.values()),
            languages=list(languages),
            preprocessing_profile=profile,
            created_at=datetime.now(tz=UTC),
            pipeline_version=__version__,
            runtime=runtime_info(),
            total_ms=round((time.perf_counter() - start) * 1000 + detection_ms, 2),
            handwriting=handwriting,
            pages_needing_review=sum(1 for p in pages if p.review.needs_review),
            pages=pages,
        )

    def run(
        self,
        document_id: str,
        engine: str,
        languages: Sequence[str],
        profile: str | None,
        compare: bool = False,
        handwriting: bool = False,
    ) -> OcrRun:
        auto_languages = self._check_languages(languages)
        if auto_languages and (self.detector is None or not self.detector.available()):
            raise AppError(
                ErrorCode.OCR_ENGINE_UNAVAILABLE,
                "Automatic language detection is unavailable; specify languages",
            )
        if handwriting and compare:
            raise AppError(ErrorCode.VALIDATION_ERROR, "compare is not available for handwriting")
        if handwriting and engine != AUTO:
            raise AppError(ErrorCode.VALIDATION_ERROR, "handwriting uses its own engine")
        manifest = self.store.read_manifest(document_id)
        if profile is not None and profile not in self.store.list_runs(document_id):
            profile = None  # no such run: OCR reads the source pages (recorded as such)

        down_reason: str | None = None
        if engine == AUTO:
            if not auto_languages and not handwriting:
                primary_name, fallback_name = self._roles(languages)
                if (
                    self._usable(primary_name, languages) is None
                    and self._usable(fallback_name, languages) is None
                ):
                    raise AppError(
                        ErrorCode.OCR_ENGINE_UNAVAILABLE,
                        "No OCR engine is available for the requested languages",
                    )
                if self._usable(primary_name, languages) is None:
                    down_reason = "primary_unavailable"
        else:
            chosen = self._engine(engine)
            reason = chosen.unavailable_reason()
            if reason is not None:
                raise AppError(ErrorCode.OCR_ENGINE_UNAVAILABLE, f"{engine}: {reason}")
            if not auto_languages:
                missing = [
                    lang
                    for lang in languages[:1]
                    if lang not in chosen.languages() or not enabled_for(engine, lang)
                ]
                if missing:
                    raise AppError(
                        ErrorCode.UNSUPPORTED_LANGUAGE,
                        f"{engine} is not enabled for: {', '.join(missing)}",
                    )

        plans = [
            self._plan(document_id, page, profile, languages, auto_languages)
            for page in manifest.pages
        ]
        run = self._run_plans(
            manifest, plans, engine, engine, languages, profile, handwriting, down_reason
        )

        if compare:
            # Also flags disagreeing pages on `run`, so it runs before the run is stored.
            self._compare(manifest, plans, run, engine, languages, profile)
        self.store.write_ocr_run(document_id, run.requested_engine, run)

        self.audit.record(
            AuditEvent.OCR_COMPLETED,
            document_id=document_id,
            engine=run.requested_engine,
            engines_used=",".join(e.name for e in run.engines),
            languages=",".join(languages),
            handwriting=handwriting,
            pages_ok=sum(1 for p in run.pages if p.status is PageOcrStatus.OK),
            pages_failed=sum(1 for p in run.pages if p.status is PageOcrStatus.FAILED),
            pages_unsupported=sum(1 for p in run.pages if p.status is PageOcrStatus.UNSUPPORTED),
            pages_fallback=sum(1 for p in run.pages if p.fallback_from),
            pages_needing_review=run.pages_needing_review,
            total_ms=round(run.total_ms),
        )
        logger.info(
            "ocr completed",
            extra={
                "document_id": document_id,
                "engine": run.requested_engine,
                "pages_needing_review": run.pages_needing_review,
                "total_ms": run.total_ms,
            },
        )
        return run

    def _compare(
        self,
        manifest: DocumentManifest,
        plans: list[PagePlan],
        run: OcrRun,
        engine: str,
        languages: Sequence[str],
        profile: str | None,
    ) -> OcrComparison:
        """Run the other engine on the same plans; flag disagreeing pages on the primary run."""
        secondary_by_page: dict[int, str | None] = {}
        for plan, page in zip(plans, run.pages, strict=True):
            if not plan.languages:
                secondary_by_page[plan.page.page_number] = None
                continue
            primary_name, fallback_name = self._roles(plan.languages)
            produced = page.engine or (primary_name if engine == AUTO else engine)
            secondary_by_page[plan.page.page_number] = (
                fallback_name if produced == primary_name else primary_name
            )
        names = {n for n in secondary_by_page.values() if n is not None}
        usable = [
            n for n in names if n in self.engines and self.engines[n].unavailable_reason() is None
        ]
        if not usable:
            raise AppError(
                ErrorCode.OCR_ENGINE_UNAVAILABLE,
                "Comparison needs a second available engine for these languages",
            )
        secondary_name = sorted(usable)[0]
        secondary_plans = [
            plan
            if secondary_by_page.get(plan.page.page_number) == secondary_name
            else PagePlan(page=plan.page, skip=plan.skip or "not_compared")
            for plan in plans
        ]
        other = self._run_plans(
            manifest, secondary_plans, secondary_name, secondary_name, languages, profile, False
        )
        self.store.write_ocr_run(manifest.document_id, other.requested_engine, other)
        comparison = compare_runs(run, other, self.threshold)
        self.store.write_ocr_comparison(manifest.document_id, comparison)
        for page, verdict in zip(run.pages, comparison.pages, strict=True):
            if verdict.needs_review and verdict.reason == "disagreement":
                page.review = _review_from(
                    [(r, d) for r, d in zip(page.review.reasons, page.review.details, strict=True)]
                    + [(ReviewReason.ENGINE_DISAGREEMENT, f"agreement {verdict.agreement}")]
                )
        run.pages_needing_review = sum(1 for p in run.pages if p.review.needs_review)
        return comparison


def _review_from(reasons: list[tuple[ReviewReason, str]]) -> PageReview:
    return PageReview(
        needs_review=bool(reasons),
        reasons=[r for r, _ in reasons],
        details=[d for _, d in reasons],
    )
