"""projects.package_baseline — fingerprint of the client package as last sent

Stores {sections:{...per-section hash...}, marked_at, marked_by} so the UI can
warn when package-affecting data (schedule, dates, contract amount, payment
terms, discussion, selections) has changed since the package was last sent.

Revision ID: 0031_project_package_baseline
Revises: 0030_project_geocode
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0031_project_package_baseline"
down_revision = "0030_project_geocode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("package_baseline", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "package_baseline")
