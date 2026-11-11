"""Record which job drafted an estimate version, change order or invoice.

⚠️ THE GAP. `scopes` and `presentations` carry `ai_job_id`. The three record
types with the most commercial weight do NOT: measured 2026-10-02, there are 29
estimate versions, 4 change orders and 8 invoices and not one of them can be
traced to the run that produced it. So nobody can answer "was this estimate
drafted by the model or written by a person", which is the first question
anybody asks about a document before it goes to a client.

⚠️ NULL MEANS "NOT RECORDED", NEVER "MADE BY A PERSON". Every existing row gets
NULL because the link was never captured, not because a human wrote it. Reading
NULL as human-authored would be a fabrication dressed as provenance, and it is
exactly the unknown-rendered-as-a-value failure this product has had to correct
elsewhere. Any surface showing this must say "not recorded" for the backlog.

ON DELETE SET NULL rather than CASCADE, deliberately: pruning old AI jobs must
never delete a client's invoice.

Revision ID: 0062_draft_provenance
Revises: 0061_proposals
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0062_draft_prov"
down_revision = "0061_proposals"
branch_labels = None
depends_on = None

TABLES = ("estimate_versions", "change_orders", "invoices")


def upgrade() -> None:
    for t in TABLES:
        op.add_column(t, sa.Column("ai_job_id", postgresql.UUID(as_uuid=True),
                                   nullable=True))
        op.create_foreign_key(
            f"fk_{t}_ai_job", t, "ai_jobs", ["ai_job_id"], ["id"],
            ondelete="SET NULL",
        )
        # Partial: the overwhelming majority of rows are NULL and always will
        # be for the backlog, so a full index would be mostly empty pages.
        op.create_index(
            f"ix_{t}_ai_job", t, ["ai_job_id"],
            postgresql_where=sa.text("ai_job_id IS NOT NULL"),
        )


def downgrade() -> None:
    for t in TABLES:
        op.drop_index(f"ix_{t}_ai_job", table_name=t)
        op.drop_constraint(f"fk_{t}_ai_job", t, type_="foreignkey")
        op.drop_column(t, "ai_job_id")
