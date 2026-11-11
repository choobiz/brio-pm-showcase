"""review_requests — log of Google-review asks (solicitation engine)

Revision ID: 0033_review_requests
Revises: 0032_project_home_warranty
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision = "0033_review_requests"
down_revision = "0032_project_home_warranty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_requests",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("requested_by", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requester_name", sa.Text(), nullable=True),
        sa.Column("recipient_name", sa.Text(), nullable=False),
        sa.Column("person_type", sa.Text(), nullable=False, server_default="customer"),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_review_requests_requested_by", "review_requests", ["requested_by"])
    op.create_index("ix_review_requests_created_at", "review_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_review_requests_created_at", table_name="review_requests")
    op.drop_index("ix_review_requests_requested_by", table_name="review_requests")
    op.drop_table("review_requests")
