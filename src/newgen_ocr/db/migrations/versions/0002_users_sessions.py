"""Web sign-in: users and sessions; document filenames for search (ADR-0030).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("disabled", sa.Boolean, nullable=False),
        sa.Column("failed_logins", sa.Integer, nullable=False),
        sa.Column("locked_until", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("password_changed_at", TS, nullable=False),
        sa.Column("last_login_at", TS),
        sa.PrimaryKeyConstraint("name", name="pk_users"),
        sa.CheckConstraint(
            "role IN ('submitter', 'viewer', 'reviewer', 'approver', 'admin')",
            name="ck_users_role",
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.String(64), nullable=False),
        sa.Column("user", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.PrimaryKeyConstraint("id_hash", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user"], ["users.name"], name="fk_sessions_user_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sessions_user", "sessions", ["user"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.add_column("documents", sa.Column("filename", sa.String(255)))


def downgrade() -> None:
    op.drop_column("documents", "filename")
    op.drop_table("sessions")
    op.drop_table("users")
