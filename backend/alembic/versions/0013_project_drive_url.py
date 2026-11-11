"""Add google_drive_url reference field to projects (Part B5 of nav redesign).

The Drive folder URL is a per-project bookmark to the source folder on Google
Drive — users still upload files manually to brio-pm's R2 storage, but the
URL gives them a one-click jump back to the original location.

Revision ID: 0013_project_drive_url
Revises: 0012_estimate_is_active
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_project_drive_url"
down_revision = "0012_estimate_is_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("google_drive_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "google_drive_url")
