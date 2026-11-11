"""projects.discussion_points — per-project client-discussion notes

Moves the client-package "What We Discussed" content from a hard-coded 1234
fallback to a per-project store. Each point is {title, body, computed?}; the
optional computed=="stormwater" point still substitutes {sw}/{site}/{total}
live from the active estimate at render time.

Revision ID: 0027_project_discussion_points
Revises: 0026_project_pre_archive_status
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0027_project_discussion_points"
down_revision = "0026_project_pre_archive_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "discussion_points",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "discussion_points")
