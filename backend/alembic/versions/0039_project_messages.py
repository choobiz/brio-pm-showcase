"""project_messages — native client↔office portal messaging

Revision ID: 0039_project_messages
Revises: 0038_client_selections
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0039_project_messages"
down_revision = "0038_client_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        # 'owner' (client) or 'office' (manager/superadmin) — drives which side
        # the bubble renders on and who the counterpart reader is.
        sa.Column("sender_role", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # When the counterpart side read it (NULL = unread by the other side).
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_project_messages_project", "project_messages", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_project_messages_project", table_name="project_messages")
    op.drop_table("project_messages")
