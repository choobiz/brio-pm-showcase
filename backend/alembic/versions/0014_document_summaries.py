"""document_summaries + add document_summary AI job kind.

Project-level document index: one row per indexed document with a
1-paragraph summary plus structured fields. The MD index for a project
is generated from these rows on demand and cached as a `project_index`
Document in R2.

Revision ID: 0014_document_summaries
Revises: 0013_project_drive_url
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_document_summaries"
down_revision = "0013_project_drive_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Extend the ai_job_kind enum with `document_summary`.
    op.execute("ALTER TYPE ai_job_kind ADD VALUE IF NOT EXISTS 'document_summary'")

    # 2) New table: one summary row per document. Cascades on document delete.
    op.create_table(
        "document_summaries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Bump when the extractor schema / prompt shape changes — lets us
        # spot stale summaries and queue a re-index without an Alembic step.
        sa.Column("skill_version", sa.String(64), nullable=False, server_default="document_summary@1"),
        # queued | running | succeeded | failed | skipped
        sa.Column("status", sa.String(32), nullable=False, server_default="queued", index=True),
        sa.Column("summary_text", sa.Text, nullable=True),
        sa.Column("structured_fields", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        # Useful for debugging / re-summarisation without re-OCRing.
        sa.Column("raw_extracted_text", sa.Text, nullable=True),
        sa.Column(
            "ai_job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_document_summaries_project_status",
        "document_summaries",
        ["project_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_summaries_project_status", table_name="document_summaries")
    op.drop_table("document_summaries")
    # NOTE: not removing the enum value — Postgres doesn't support that
    # cleanly. Future migrations can leave it dangling without harm.
