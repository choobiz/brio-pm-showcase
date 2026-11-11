"""quantity_overrides — verification state, so a machine candidate cannot pass as human-verified

Revision ID: 0045_qty_override_verify
Revises: 0044_payment_term_templates
Create Date: 2026-08-30

`takeoff_to_overrides` writes CAD-extracted quantities with the note "Reconcile vs
PDF & confirm before estimate", but `_verified_quantities` selected every row for
the discipline with no filter, and its own docstring called the result "the
human-verified ground truth from the review UI". There was no column that could
tell the two apart, so there was no way for the estimator to know which was which.

Existing rows are grandfathered as `confirmed`: they have already been priced and
reviewed in whatever way they were, and re-opening every historical project is not
what this change is for. The gate applies to extractions from here on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# Alembic stores this in alembic_version.version_num, a varchar(32).
# The descriptive name did not fit and the whole migration rolled back.
revision = "0045_qty_override_verify"
down_revision = "0044_payment_term_templates"
branch_labels = None
depends_on = None

STATUSES = ("candidate", "confirmed", "corrected", "excluded")


def upgrade() -> None:
    status = sa.Enum(*STATUSES, name="quantity_override_status")
    status.create(op.get_bind(), checkfirst=True)
    op.add_column("quantity_overrides", sa.Column(
        "status", status, nullable=False, server_default="confirmed"))
    op.add_column("quantity_overrides", sa.Column(
        "machine_value", sa.Numeric(14, 2), nullable=True))
    op.add_column("quantity_overrides", sa.Column(
        "confirmed_by_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("quantity_overrides", sa.Column(
        "confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_quantity_overrides_confirmed_by", "quantity_overrides", "users",
        ["confirmed_by_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_quantity_overrides_project_status", "quantity_overrides",
                    ["project_id", "status"])
    # New rows default to candidate; existing rows keep the grandfathered value
    # already written above.
    op.alter_column("quantity_overrides", "status", server_default="candidate")


def downgrade() -> None:
    op.drop_index("ix_quantity_overrides_project_status", table_name="quantity_overrides")
    op.drop_constraint("fk_quantity_overrides_confirmed_by", "quantity_overrides",
                       type_="foreignkey")
    for col in ("confirmed_at", "confirmed_by_id", "machine_value", "status"):
        op.drop_column("quantity_overrides", col)
    sa.Enum(name="quantity_override_status").drop(op.get_bind(), checkfirst=True)
