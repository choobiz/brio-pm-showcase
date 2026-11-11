"""project_communications — per-project communication log (Conversations)

Revision ID: 0040_project_communications
Revises: 0039_project_messages
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0040_project_communications"
down_revision = "0039_project_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_communications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        # How it entered the system: manual paste/upload, BCC address, or forwarded email.
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        # inbound (from client/trade) | outbound (from us) | internal
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),  # email | sms | call | meeting | note
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("from_email", sa.Text(), nullable=True),
        sa.Column("to_emails", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        # When the communication actually happened (may differ from created_at).
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        # AI-assigned classification + extraction.
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("action_items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("urgency", sa.Text(), nullable=True),          # low | normal | high
        sa.Column("has_quote", sa.Boolean(), nullable=False, server_default="false"),
        # Whether the AI classification has run yet.
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_project_communications_project",
                    "project_communications", ["project_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_project_communications_project", table_name="project_communications")
    op.drop_table("project_communications")
