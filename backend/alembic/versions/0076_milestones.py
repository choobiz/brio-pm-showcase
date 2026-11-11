"""Milestone bookings and letters: project team, milestones, emails

Amir approved 2026-10-16 ("Plan: Milestone Bookings and Letters").

Revision ID: 0076_milestones
Revises: 0075_catalogue_imports
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0076_milestones"
down_revision = "0075_catalogue_imports"
branch_labels = None
depends_on = None


def _base():
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _project():
    return sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                     nullable=False, index=True)


def upgrade() -> None:
    op.create_table(
        "project_team", *_base(), _project(),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()), sa.Column("company", sa.Text()), sa.Column("email", sa.Text()),
        sa.Column("phone", sa.Text()), sa.Column("notes", sa.Text()),
    )
    op.create_table(
        "project_milestones", *_base(), _project(),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("template_key", sa.Text()),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("stage_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("party_role", sa.Text(), nullable=False),
        sa.Column("email_kind", sa.Text(), nullable=False, server_default="booking"),
        sa.Column("status", sa.Text(), nullable=False, server_default="to_book"),
        sa.Column("lead_days", sa.Integer()), sa.Column("lead_note", sa.Text()),
        sa.Column("activity_code", sa.Text()),
        sa.Column("required_docs", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("why", sa.Text()), sa.Column("source", sa.Text()),
        sa.Column("booked_for", sa.Date()), sa.Column("notes", sa.Text()),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("thread", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
        sa.UniqueConstraint("project_id", "ref", name="uq_project_milestones_ref"),
    )
    op.create_table(
        "milestone_emails", *_base(), _project(),
        sa.Column("milestone_id", pg.UUID(as_uuid=True), sa.ForeignKey("project_milestones.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("to_email", sa.Text()), sa.Column("to_name", sa.Text()), sa.Column("cc", sa.Text()),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("attachment_ids", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("error", sa.Text()), sa.Column("ai_job_id", pg.UUID(as_uuid=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)), sa.Column("sent_by", sa.Text()),
        sa.Column("sent_envelope", pg.JSONB()),
    )


def downgrade() -> None:
    op.drop_table("milestone_emails")
    op.drop_table("project_milestones")
    op.drop_table("project_team")
