"""Skill version approval audit columns.

Adds approved_by / approved_at / approval_note to skill_versions so the
approval gate (Step 3) can record who signed off on each active prompt and
when. A draft (candidate) version becomes active only via an explicit
approve action; this captures the audit trail.

Existing active rows are backfilled as approved by 'system (seed)' — they're
the v1.0 prompts already running, mirrored from code.

Revision ID: 0017_skill_version_approval
Revises: 0016_project_index_documents
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0017_skill_version_approval"
down_revision = "0016_project_index_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_versions",
        sa.Column("approved_by", sa.String(64), nullable=True),
    )
    op.add_column(
        "skill_versions",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skill_versions",
        sa.Column("approval_note", sa.Text, nullable=True),
    )
    # Backfill: the active v1.0 rows are the prompts already running.
    op.execute(
        "UPDATE skill_versions "
        "SET approved_by = 'system (seed)', approved_at = now() "
        "WHERE status = 'active' AND approved_by IS NULL"
    )


def downgrade() -> None:
    op.drop_column("skill_versions", "approval_note")
    op.drop_column("skill_versions", "approved_at")
    op.drop_column("skill_versions", "approved_by")
