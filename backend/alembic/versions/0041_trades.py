"""trades — cross-project trade / subcontractor / supplier directory (Trade Library)

Revision ID: 0041_trades
Revises: 0040_project_communications
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0041_trades"
down_revision = "0040_project_communications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("normalized_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("trade_category", sa.Text(), nullable=False, server_default="other"),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("emails", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("normalized_emails", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("phones", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("normalized_phones", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        # unconfirmed (AI-extracted, pending review) | active | archived
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),  # manual | communication
        sa.Column("source_communication_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("project_communications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("first_project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_trades_normalized_name", "trades", ["normalized_name"])
    op.create_index("ix_trades_trade_category", "trades", ["trade_category"])
    op.create_index("ix_trades_status", "trades", ["status"])


def downgrade() -> None:
    op.drop_index("ix_trades_status", table_name="trades")
    op.drop_index("ix_trades_trade_category", table_name="trades")
    op.drop_index("ix_trades_normalized_name", table_name="trades")
    op.drop_table("trades")
