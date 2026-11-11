"""change_orders — owner-approved changes with cost + schedule impact

Office creates a CO (draft) → submits (pending) → owner approves/rejects in the
portal. Approved COs adjust the client's revised contract total.

Revision ID: 0036_change_orders
Revises: 0035_document_client_visible
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0036_change_orders"
down_revision = "0035_document_client_visible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "change_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cost_impact", sa.Numeric(12, 2), nullable=False, server_default="0"),   # pre-tax; +add / -credit
        sa.Column("schedule_impact_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),  # draft/pending/approved/rejected/void
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "number", name="uq_change_orders_project_number"),
    )
    op.create_index("ix_change_orders_project", "change_orders", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_change_orders_project", table_name="change_orders")
    op.drop_table("change_orders")
