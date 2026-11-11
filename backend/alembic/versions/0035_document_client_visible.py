"""documents.client_visible — curate what the owner portal shows

Default false (nothing leaks); backfill contract/permit to true. Managers
share photos + other docs explicitly from the office.

Revision ID: 0035_document_client_visible
Revises: 0034_roles_and_active
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0035_document_client_visible"
down_revision = "0034_roles_and_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("client_visible", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Conservative backfill: only the clearly client-facing categories.
    op.execute("UPDATE documents SET client_visible = true WHERE category IN ('contract', 'permit')")


def downgrade() -> None:
    op.drop_column("documents", "client_visible")
