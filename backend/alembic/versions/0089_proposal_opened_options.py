"""Client › Proposal: when the client opened it, and which options they kept
when accepting (round 2 board R2Proposal).

Revision ID: 0089_proposal_opened_options
Revises: 0088_contract_signed_copy
"""
from __future__ import annotations

from alembic import op

revision = "0089_proposal_opened_options"
down_revision = "0088_contract_signed_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE presentations
        ADD COLUMN IF NOT EXISTS portal_opened_at timestamptz,
        ADD COLUMN IF NOT EXISTS portal_open_count integer NOT NULL DEFAULT 0""")
    op.execute("ALTER TABLE proposal_acceptances ADD COLUMN IF NOT EXISTS options jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE proposal_acceptances DROP COLUMN IF EXISTS options")
    op.execute("ALTER TABLE presentations DROP COLUMN IF EXISTS portal_open_count, DROP COLUMN IF EXISTS portal_opened_at")
