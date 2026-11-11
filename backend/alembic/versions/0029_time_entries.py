"""time_entries — employee punch-in/out with GPS

One open entry per user (partial unique index) so nobody is clocked into two
projects at once. Punch-in/out each store lat/lng/accuracy for location review.

Revision ID: 0029_time_entries
Revises: 0028_daily_site_reports
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0029_time_entries"
down_revision = "0028_daily_site_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "time_entries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("punch_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("punch_in_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("punch_in_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("punch_in_accuracy", sa.Numeric(8, 1), nullable=True),
        sa.Column("punch_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("punch_out_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("punch_out_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("punch_out_accuracy", sa.Numeric(8, 1), nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("source", sa.Text, nullable=False, server_default="field"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_time_entries_user", "time_entries", ["user_id"])
    op.create_index("ix_time_entries_project", "time_entries", ["project_id"])
    # One open punch per user, across all projects.
    op.create_index(
        "uq_time_entries_one_open_per_user",
        "time_entries",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_time_entries_one_open_per_user", table_name="time_entries")
    op.drop_index("ix_time_entries_project", table_name="time_entries")
    op.drop_index("ix_time_entries_user", table_name="time_entries")
    op.drop_table("time_entries")
