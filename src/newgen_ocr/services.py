"""The application's services, built once per process (API or worker).

Local mode keeps everything on the filesystem. Service mode (ADR-0029) keeps tokens, audit
events, review items and history, job state and document metadata in PostgreSQL; originals,
page images and OCR/layout/extraction JSON stay on the shared data volume.
"""

from __future__ import annotations

from dataclasses import dataclass

from newgen_ocr.audit import AuditLog
from newgen_ocr.auth.db import DbTokenStore
from newgen_ocr.auth.tokens import TokenStore
from newgen_ocr.config import Settings
from newgen_ocr.db.audit import DbAuditLog
from newgen_ocr.db.engine import Database
from newgen_ocr.db.review_store import SqlReviewStore
from newgen_ocr.extraction.extract import Options
from newgen_ocr.extraction.schema import SchemaRegistry
from newgen_ocr.extraction.service import ExtractionService
from newgen_ocr.ingestion.service import IngestionService
from newgen_ocr.jobs.repo import JobRepo
from newgen_ocr.layout.detector import PaddleLayoutModels
from newgen_ocr.layout.service import LayoutPolicy, LayoutService
from newgen_ocr.ocr.engine import OcrEngine
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.paddle_engine import PaddleEngine
from newgen_ocr.ocr.script_detect import ScriptDetector
from newgen_ocr.ocr.service import HANDWRITING_ENGINE, OcrService, ReviewPolicy
from newgen_ocr.ocr.tesseract_engine import TesseractEngine
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.review.acceptance import Policy
from newgen_ocr.review.calibration import load_calibration
from newgen_ocr.review.service import ReviewService
from newgen_ocr.review.store import ReviewStore
from newgen_ocr.storage.local import DocumentStore


@dataclass
class Services:
    settings: Settings
    store: DocumentStore
    audit: AuditLog
    tokens: TokenStore | DbTokenStore
    ingestion: IngestionService
    preprocessing: PreprocessingService
    ocr: OcrService
    layout: LayoutService
    extraction: ExtractionService
    review: ReviewService
    db: Database | None = None
    jobs: JobRepo | None = None


def build_engines(settings: Settings) -> dict[str, OcrEngine]:
    """Engines are cheap to construct; models load lazily on first use."""
    models = ModelStore(settings.models_dir, settings.models_lock)
    return {
        "paddle": PaddleEngine(
            models,
            cache_dir=settings.data_dir / "cache" / "paddlex",
            detection_model=settings.paddle_detection_model,
            cpu_threads=settings.ocr_cpu_threads,
            enable_mkldnn=settings.paddle_enable_mkldnn,
        ),
        "tesseract": TesseractEngine(
            models,
            command=settings.tesseract_cmd,
            timeout_s=settings.ocr_page_timeout_s,
            threads=settings.ocr_cpu_threads,
        ),
        HANDWRITING_ENGINE: PaddleEngine(
            models,
            cache_dir=settings.data_dir / "cache" / "paddlex",
            detection_model=settings.paddle_detection_model,
            cpu_threads=settings.ocr_cpu_threads,
            enable_mkldnn=settings.paddle_enable_mkldnn,
            name=HANDWRITING_ENGINE,
            recognition_kind="handwriting_recognition",
        ),
    }


def build_services(settings: Settings, pool_size: int = 5) -> Services:
    calibration = load_calibration(settings.review_calibration_file)
    if (
        settings.review_auto_accept
        and calibration is not None
        and calibration.dataset.kind == "synthetic"
        and settings.environment == "production"
        and not settings.review_allow_synthetic_calibration
    ):
        raise ValueError(
            "review_auto_accept in production with a synthetic calibration: calibrate on real "
            "documents, or set review_allow_synthetic_calibration knowingly"
        )
    db: Database | None = None
    jobs: JobRepo | None = None
    audit: AuditLog
    tokens: TokenStore | DbTokenStore
    reviews: ReviewStore
    if settings.database_url is not None:
        db = Database(settings.database_url, pool_size=pool_size)
        jobs = JobRepo(db, max_attempts=settings.job_max_attempts)
        audit = DbAuditLog(db)
        tokens = DbTokenStore(db)
        reviews = SqlReviewStore(db)
    else:
        audit = AuditLog(settings.data_dir)
        tokens = TokenStore(settings.auth_tokens_path)
        reviews = ReviewStore(settings.data_dir)

    store = DocumentStore(settings.data_dir)
    engines = build_engines(settings)
    ocr = OcrService(
        store,
        audit,
        engines,
        primary=settings.ocr_primary_engine,
        fallback=settings.ocr_fallback_engine,
        disagreement_threshold=settings.ocr_disagreement_threshold,
        language_primary=dict(settings.ocr_language_primary),
        detector=ScriptDetector(
            ModelStore(settings.models_dir, settings.models_lock),
            command=settings.tesseract_cmd,
            timeout_s=settings.ocr_page_timeout_s,
        ),
        detect_orientation=settings.ocr_detect_orientation,
        review_policy=ReviewPolicy(
            min_page_confidence=settings.ocr_min_page_confidence,
            min_line_confidence=settings.ocr_min_line_confidence,
            handwriting_verified=frozenset(settings.ocr_handwriting_verified_languages),
        ),
    )
    layout = LayoutService(
        store,
        audit,
        PaddleLayoutModels(
            ModelStore(settings.models_dir, settings.models_lock),
            cache_dir=settings.data_dir / "cache" / "paddlex",
            layout_model=settings.layout_model,
            score_threshold=settings.layout_score_threshold,
            cpu_threads=settings.ocr_cpu_threads,
            enable_mkldnn=settings.paddle_enable_mkldnn,
        ),
        engines,
        LayoutPolicy(
            min_table_score=settings.layout_min_table_score,
            ruling_check=settings.layout_ruling_check,
        ),
    )
    registry = SchemaRegistry(settings.extraction_schema_dir)
    options = Options(
        day_first=settings.extraction_day_first,
        mask_aadhaar=settings.extraction_mask_aadhaar,
    )
    extraction = ExtractionService(
        store,
        audit,
        registry,
        options,
        min_evidence_confidence=settings.extraction_min_evidence_confidence,
    )
    review = ReviewService(
        store,
        reviews,
        audit,
        registry,
        Policy(calibration=calibration, auto_accept=settings.review_auto_accept, options=options),
        require_second_person=settings.review_require_second_person,
    )
    return Services(
        settings=settings,
        store=store,
        audit=audit,
        tokens=tokens,
        ingestion=IngestionService(settings, store, audit),
        preprocessing=PreprocessingService(store, audit, settings.preprocessing_profiles),
        ocr=ocr,
        layout=layout,
        extraction=extraction,
        review=review,
        db=db,
        jobs=jobs,
    )
