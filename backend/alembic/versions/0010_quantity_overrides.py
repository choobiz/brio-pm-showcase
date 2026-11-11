"""quantity_overrides table — user-verified per-project/per-discipline quantities.

These are authoritative over AI-extracted values when present. The review UI
writes one row per confirmed candidate (source='candidate_confirmed'), per
manually drawn line (source='manual_measure'), or per typed override
(source='manual_entry'). Estimate generation prefers these when calculating
per-discipline totals.

Revision ID: 0010_quantity_overrides
Revises: 0009_drawing_analyses
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_quantity_overrides"
down_revision: Union[str, None] = "0009_drawing_analyses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quantity_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=False),
        sa.Column("quantity_type", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_candidate_key", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_quantity_overrides_project_disc", "quantity_overrides",
                    ["project_id", "discipline"])
    op.create_index("ix_quantity_overrides_doc_candidate", "quantity_overrides",
                    ["source_document_id", "source_candidate_key"], unique=True,
                    postgresql_where=sa.text("source_candidate_key IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_quantity_overrides_doc_candidate", table_name="quantity_overrides")
    op.drop_index("ix_quantity_overrides_project_disc", table_name="quantity_overrides")
    op.drop_table("quantity_overrides")
