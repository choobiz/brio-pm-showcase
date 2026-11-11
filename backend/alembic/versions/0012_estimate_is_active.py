"""Add is_active flag to estimate_versions (Phase 3a — workbench anchoring).

One estimate per project may be marked is_active=true; downstream artifacts
(SOV, cash flow, contract) display "out-of-sync" when their based_on_estimate_id
points to a different version. Enforced by a partial unique index on
(project_id) WHERE is_active = true.

Revision ID: 0012_estimate_is_active
Revises: 0011_workbench_schema
Create Date: 2026-06-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_estimate_is_active"
down_revision = "0011_workbench_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "estimate_versions",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Partial unique index — at most one active row per project.
    op.create_index(
        "uq_estimate_versions_active_per_project",
        "estimate_versions",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_estimate_versions_active_per_project",
        table_name="estimate_versions",
    )
    op.drop_column("estimate_versions", "is_active")
