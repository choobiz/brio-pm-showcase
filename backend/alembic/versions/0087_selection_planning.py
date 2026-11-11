"""Selections planned against the schedule (round 2 board R2Selections):
the activity a decision is needed for, the supplier's lead time, the
allowance, when it was sent to the client, and the change order a confirmed
choice made.

Revision ID: 0087_selection_planning
Revises: 0086_proposal_portal
"""
from __future__ import annotations

from alembic import op

revision = "0087_selection_planning"
down_revision = "0086_proposal_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE client_selections
        ADD COLUMN IF NOT EXISTS activity_code text,
        ADD COLUMN IF NOT EXISTS lead_time_days integer,
        ADD COLUMN IF NOT EXISTS allowance numeric(12,2),
        ADD COLUMN IF NOT EXISTS sent_at timestamptz,
        ADD COLUMN IF NOT EXISTS change_order_id uuid REFERENCES change_orders(id) ON DELETE SET NULL""")
    # Before this, adding a selection sent it to the client at once.
    op.execute("UPDATE client_selections SET sent_at = created_at WHERE sent_at IS NULL")


def downgrade() -> None:
    op.execute("""ALTER TABLE client_selections DROP COLUMN IF EXISTS change_order_id,
        DROP COLUMN IF EXISTS sent_at, DROP COLUMN IF EXISTS allowance,
        DROP COLUMN IF EXISTS lead_time_days, DROP COLUMN IF EXISTS activity_code""")
