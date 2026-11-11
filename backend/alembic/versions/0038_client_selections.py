"""client_selections — owner-portal material selections & approvals

Revision ID: 0038_client_selections
Revises: 0037_project_summary
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0038_client_selections"
down_revision = "0037_project_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # 'select' → choose one option; 'approve' → approve / request changes.
        sa.Column("decision_type", sa.Text(), nullable=False, server_default="select"),
        # Who purchases the material: 'brio' | 'client' | NULL (undecided).
        sa.Column("purchaser", sa.Text(), nullable=True),
        # [{id, label, description, price_delta}]
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default="[]"),
        # 'pending' → awaiting client; 'submitted' → client decided; 'confirmed' → office locked.
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("selected_option_id", sa.Text(), nullable=True),
        sa.Column("client_comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_client_selections_project", "client_selections", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_client_selections_project", table_name="client_selections")
    op.drop_table("client_selections")
