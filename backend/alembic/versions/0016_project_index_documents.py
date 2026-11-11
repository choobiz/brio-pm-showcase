"""Per-project index_documents flag.

Adds projects.index_documents (BOOL NOT NULL DEFAULT TRUE). The
auto-index queue skips any project where this is false — useful for
archived / historical-data dumps that shouldn't burn tokens on every
upload.

Pre-flags `357-359 East 4th (Occupancy Permit -2021)` to false because
its 4,019-doc historical permit archive ate most of the cross-project
backfill budget on 2026-06-18. Anyone re-uploading should toggle the
flag back on consciously.

Revision ID: 0016_project_index_documents
Revises: 0015_skill_engine
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0016_project_index_documents"
down_revision = "0015_skill_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "index_documents",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Flag the known historical archive off so the auto-trigger stops
    # hitting it on every future upload.
    op.execute(
        "UPDATE projects SET index_documents = false "
        "WHERE name = '357-359 East 4th (Occupancy Permit -2021)'"
    )


def downgrade() -> None:
    op.drop_column("projects", "index_documents")
