"""Add a soft-delete flag to documents.

Track B deletes documents by archiving, never hard-deleting — the R2 object
and the audit trail must survive a "delete" in the UI.

Revision ID: 0005_documents_archived
Revises: 0004_scope_ai_job_link
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_documents_archived"
down_revision: Union[str, None] = "0004_scope_ai_job_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "archived")
