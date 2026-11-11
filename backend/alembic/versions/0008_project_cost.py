"""project_costs table — the 'actuals' side of estimate-vs-actual.

A ProjectCost is a cost BRIO received (subtrade/supplier invoice, or a
subtrade quote). Kept separate from `invoices` (BRIO -> client) so the Drive
migration never conflates the two money directions.

Revision ID: 0008_project_cost
Revises: 0007_invoice_number_seq
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_project_cost"
down_revision: Union[str, None] = "0007_invoice_number_seq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_costs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="other"),
        sa.Column("vendor", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("cost_date", sa.Date(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_project_costs_project_id", "project_costs", ["project_id"])
    op.create_index("ix_project_costs_source_document_id", "project_costs",
                    ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_project_costs_source_document_id", table_name="project_costs")
    op.drop_index("ix_project_costs_project_id", table_name="project_costs")
    op.drop_table("project_costs")
