"""projects.pre_archive_status — retain the status a project had before archiving

So Unarchive can restore the exact prior status (lead/preconstruction/in_progress/
closeout) instead of resetting to a neutral default. Set on archive, cleared on
unarchive.

Revision ID: 0026_project_pre_archive_status
Revises: 0025_rate_library
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0026_project_pre_archive_status"
down_revision = "0025_rate_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("pre_archive_status", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "pre_archive_status")
