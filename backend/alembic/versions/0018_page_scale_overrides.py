"""page_scale_overrides table — user-set drawing scale per (document, page).

Auto-detection fails on drawings whose title-block scale is vectorized text
or in an uncovered notation (metric / decimal-foot sets). This table lets a
human pin the scale (named scale or two-point calibration) so the measured
takeoff can compute real feet. Authoritative over the auto-detected scale.

Revision ID: 0018_page_scale_overrides
Revises: 0017_skill_version_approval
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0018_page_scale_overrides"
down_revision: Union[str, None] = "0017_skill_version_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_scale_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("pdf_units_per_real_foot", sa.Numeric(14, 4), nullable=False),
        sa.Column("scale_label", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("document_id", "page", name="uq_page_scale_doc_page"),
    )
    op.create_index("ix_page_scale_overrides_document_id", "page_scale_overrides",
                    ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_page_scale_overrides_document_id", table_name="page_scale_overrides")
    op.drop_table("page_scale_overrides")
