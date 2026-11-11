"""projects.summary — short client-facing project description for the package overview

Revision ID: 0037_project_summary
Revises: 0036_change_orders
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0037_project_summary"
down_revision = "0036_change_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "summary")
