"""projects.inbound_token — per-project inbound-email address token (Conversations P2)

Revision ID: 0042_project_inbound_token
Revises: 0041_trades
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0042_project_inbound_token"
down_revision = "0041_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("inbound_token", sa.Text(), nullable=True))
    op.create_index("ix_projects_inbound_token", "projects", ["inbound_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_projects_inbound_token", table_name="projects")
    op.drop_column("projects", "inbound_token")
