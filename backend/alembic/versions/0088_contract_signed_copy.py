"""Contract: the owner-countersigned copy on file (round 2 board R2Contract).

Revision ID: 0088_contract_signed_copy
Revises: 0087_selection_planning
"""
from __future__ import annotations

from alembic import op

revision = "0088_contract_signed_copy"
down_revision = "0087_selection_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signed_copy_document_id uuid
                  REFERENCES documents(id) ON DELETE SET NULL""")


def downgrade() -> None:
    op.execute("ALTER TABLE contracts DROP COLUMN IF EXISTS signed_copy_document_id")
