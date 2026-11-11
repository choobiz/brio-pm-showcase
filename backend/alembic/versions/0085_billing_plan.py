"""Billing plan: when each signed contract stage is billed, and the inputs the
schedule of values and cash flow are built from.

The signed contract row is not changed except for one new field, the actual
date of substantial completion (the holdback is released 55 days after it).

Revision ID: 0085_billing_plan
Revises: 0084_cost_entry
"""
from __future__ import annotations

from alembic import op

revision = "0085_billing_plan"
down_revision = "0084_cost_entry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contracts ADD COLUMN IF NOT EXISTS substantial_completion_actual date")
    op.execute("""
        CREATE TABLE billing_plans (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id uuid NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
            contract_id uuid REFERENCES contracts(id) ON DELETE SET NULL,
            stage_activities jsonb NOT NULL DEFAULT '{}'::jsonb,
            division_phases jsonb NOT NULL DEFAULT '{}'::jsonb,
            cost_share_pct numeric(5,2) NOT NULL DEFAULT 85,
            owner_net_days integer NOT NULL DEFAULT 0,
            created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
            updated_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS billing_plans")
    op.execute("ALTER TABLE contracts DROP COLUMN IF EXISTS substantial_completion_actual")
