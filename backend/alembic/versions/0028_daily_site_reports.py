"""daily_site_reports — schedule-driven on-site daily log

One report per (project, day), pre-filled from the active schedule's activities
overlapping the day and reconciled (data-preserving) when the schedule shifts.
The schedule-impact analysis (slip detection + AI note-risk) is computed on
demand from these reports + the schedule, so no separate table is needed.

Revision ID: 0028_daily_site_reports
Revises: 0027_project_discussion_points
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0028_daily_site_reports"
down_revision = "0027_project_discussion_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_site_reports",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schedule_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_by_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "report_date", name="uq_daily_report_project_date"),
    )
    op.create_index("ix_daily_site_reports_project", "daily_site_reports", ["project_id"])
    op.create_index("ix_daily_site_reports_date", "daily_site_reports", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_site_reports_date", table_name="daily_site_reports")
    op.drop_index("ix_daily_site_reports_project", table_name="daily_site_reports")
    op.drop_table("daily_site_reports")
