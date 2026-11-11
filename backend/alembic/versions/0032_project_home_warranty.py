"""projects.home_warranty_required — flags a "new home" (dwelling) requiring 2-5-10

When true, the contract discloses the mandatory BC third-party home warranty
(2-5-10, Travelers). Off for accessory buildings (garage/shop/office).

Revision ID: 0032_project_home_warranty
Revises: 0031_project_package_baseline
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0032_project_home_warranty"
down_revision = "0031_project_package_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("home_warranty_required", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("projects", "home_warranty_required")
