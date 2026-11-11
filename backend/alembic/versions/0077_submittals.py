"""Submittal review: shop drawings and quotes checked against governing documents

Wireframes approved by Amir 2026-10-17.

Revision ID: 0077_submittals
Revises: 0076_milestones
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0077_submittals"
down_revision = "0076_milestones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    j = lambda name, default: sa.Column(name, pg.JSONB(), nullable=False, server_default=default)  # noqa: E731
    op.create_table(
        "submittals",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("trade", sa.Text(), nullable=False),
        sa.Column("supplier", sa.Text()),
        sa.Column("revision", sa.Text(), nullable=False, server_default="REV 0"),
        sa.Column("parent_id", pg.UUID(as_uuid=True), sa.ForeignKey("submittals.id", ondelete="SET NULL")),
        j("submittal_doc_ids", "[]"), j("governing_doc_ids", "[]"),
        sa.Column("instructions", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("error", sa.Text()),
        sa.Column("ai_job_id", pg.UUID(as_uuid=True)),
        sa.Column("approved_usd", sa.Numeric(10, 4)),
        sa.Column("cost_usd", sa.Numeric(10, 4)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text()),
        j("findings", "[]"), j("quote", "{}"),
        sa.Column("outcome", sa.Text()),
        sa.Column("outcome_note", sa.Text()),
        sa.Column("outcome_by", sa.Text()),
        sa.Column("outcome_at", sa.DateTime(timezone=True)),
        sa.Column("signed_received", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("milestone_id", pg.UUID(as_uuid=True), sa.ForeignKey("project_milestones.id", ondelete="SET NULL")),
        j("emails", "[]"), j("thread", "[]"), j("history", "[]"),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_table("submittals")
