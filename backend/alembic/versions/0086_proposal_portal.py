"""Client › Proposal: send a presentation to the client portal, and record the
client's acceptance (who typed their name, when, from where, and the price
they saw). Accepting does not change any contract.

Revision ID: 0086_proposal_portal
Revises: 0085_billing_plan
"""
from __future__ import annotations

from alembic import op

revision = "0086_proposal_portal"
down_revision = "0085_billing_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE presentations
        ADD COLUMN IF NOT EXISTS portal_sent_at timestamptz,
        ADD COLUMN IF NOT EXISTS portal_sent_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS portal_withdrawn_at timestamptz""")
    op.execute("""
        CREATE TABLE proposal_acceptances (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            presentation_id uuid NOT NULL UNIQUE REFERENCES presentations(id) ON DELETE CASCADE,
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id uuid REFERENCES users(id) ON DELETE SET NULL,
            typed_name text NOT NULL,
            version_number integer,
            subtotal numeric(12,2),
            gst numeric(12,2),
            total numeric(12,2),
            valid_until date,
            ip text,
            user_agent text,
            accepted_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_proposal_acceptances_project ON proposal_acceptances (project_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS proposal_acceptances")
    op.execute("""ALTER TABLE presentations DROP COLUMN IF EXISTS portal_withdrawn_at,
        DROP COLUMN IF EXISTS portal_sent_by_id, DROP COLUMN IF EXISTS portal_sent_at""")
