"""Initial schema: tokens, documents, pages, jobs, results, review, audit (ADR-0029).

Revision ID: 0001
Revises:
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)

# Append-only tables: updates and deletes are refused by the database itself, whatever the
# application does. (TRUNCATE is a separate privilege: production grants the application
# role INSERT/SELECT only; OPERATIONS_RUNBOOK.md.)
_FORBID = """
CREATE FUNCTION newgen_forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("user", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS),
        sa.Column("revoked_at", TS),
        sa.PrimaryKeyConstraint("id", name="pk_api_tokens"),
    )
    op.create_index("ix_api_tokens_user", "api_tokens", ["user"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("format", sa.String(8)),
        sa.Column("page_count", sa.Integer),
        sa.Column("submitted_by", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("manifest", JSONB),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    for column in ("status", "sha256", "submitted_by", "created_at"):
        op.create_index(f"ix_documents_{column}", "documents", [column])

    op.create_table(
        "pages",
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("needs_ocr", sa.Boolean, nullable=False),
        sa.Column("width_px", sa.Integer),
        sa.Column("height_px", sa.Integer),
        sa.Column("quality", JSONB),
        sa.PrimaryKeyConstraint("document_id", "page_number", name="pk_pages"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_pages_document_id_documents",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stages", JSONB, nullable=False),
        sa.Column("completed_stages", JSONB, nullable=False),
        sa.Column("current_stage", sa.String(16)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("lease_expires_at", TS),
        sa.Column("worker", sa.String(128)),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("error_message", sa.String(300)),
        sa.Column("submitted_by", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("not_before", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_jobs_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="ck_jobs_attempts"),
    )
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"])
    op.create_index("ix_jobs_submitted_by", "jobs", ["submitted_by"])
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index(
        "uq_jobs_active_document",
        "jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "uq_jobs_idempotency",
        "jobs",
        ["submitted_by", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("job_id", sa.String(32), nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16)),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("detail", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_events_job_id_jobs", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])

    op.create_table(
        "extraction_results",
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("layout_run", sa.String(32), nullable=False),
        sa.Column("schema_id", sa.String(48)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("document_id", "layout_run", name="pk_extraction_results"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_extraction_results_document_id_documents",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_extraction_results_schema_id", "extraction_results", ["schema_id"])
    op.create_index("ix_extraction_results_status", "extraction_results", ["status"])

    op.create_table(
        "review_items",
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("layout_run", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("schema_id", sa.String(48)),
        sa.Column("assigned_to", sa.String(64)),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("item", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("document_id", "layout_run", name="pk_review_items"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_review_items_document_id_documents",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_review_items_status", "review_items", ["status"])

    op.create_table(
        "review_item_revisions",
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("layout_run", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("item", JSONB, nullable=False),
        sa.PrimaryKeyConstraint(
            "document_id", "layout_run", "revision", name="pk_review_item_revisions"
        ),
    )

    op.create_table(
        "review_history",
        sa.Column("seq", sa.BigInteger, autoincrement=False, nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(32), nullable=False),
        sa.Column("layout_run", sa.String(32), nullable=False),
        sa.Column("body", JSONB, nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name="pk_review_history"),
    )
    op.create_index("ix_review_history_document_id", "review_history", ["document_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("document_id", sa.String(32)),
        sa.Column("fields", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    for column in ("ts", "event", "document_id"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])

    op.execute(_FORBID)
    for table in ("review_history", "audit_events"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION newgen_forbid_change()"
        )


def downgrade() -> None:
    for table in ("review_history", "audit_events"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS newgen_forbid_change()")
    for table in (
        "audit_events",
        "review_history",
        "review_item_revisions",
        "review_items",
        "extraction_results",
        "job_events",
        "jobs",
        "pages",
        "documents",
        "api_tokens",
    ):
        op.drop_table(table)
