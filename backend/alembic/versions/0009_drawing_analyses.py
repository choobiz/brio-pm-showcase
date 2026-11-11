"""drawing_analyses table — cached per-document PDF parse results.

One row per Document (unique). Re-parses are triggered when the SHA-256 of
the R2 object changes (e.g. the user replaces a drawing with a revised set).

Revision ID: 0009_drawing_analyses
Revises: 0008_project_cost
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009_drawing_analyses"
down_revision: Union[str, None] = "0008_project_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drawing_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("is_vector", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("page_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("payload", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("drawing_analyses")
