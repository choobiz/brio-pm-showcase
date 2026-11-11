"""Estimate versioning + originating-job link.

Track D keeps multiple Estimate rows per project (v1, v2, …); the highest
version is the active one for invoicing. `ai_job_id` links an estimate back
to the `estimate_generation` job that produced it (NULL for hand-created).

Revision ID: 0006_estimate_version_job
Revises: 0005_documents_archived
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_estimate_version_job"
down_revision: Union[str, None] = "0005_documents_archived"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "estimates",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "estimates",
        sa.Column(
            "ai_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("estimates", "ai_job_id")
    op.drop_column("estimates", "version")
