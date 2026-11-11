"""Add the change_order_generation AI job kind.

NB: the revision id is kept short on purpose. alembic_version.version_num is
varchar(32) and "0049_change_order_generation_kind" is 33 characters, so the
first attempt applied nothing and failed on the bookkeeping UPDATE.

Change orders had no generation path at all while estimates had four. This adds
the enum value the new job needs.

ALTER TYPE ... ADD VALUE cannot run inside a transaction block on PostgreSQL
before 12, and Alembic wraps a migration in one, so the connection is committed
first. The guard makes it re-runnable: adding a value that already exists is an
error, not a no-op.

Revision ID: 0049_change_order_kind
Revises: 0048_crm_module
"""
from alembic import op

revision = "0049_change_order_kind"
down_revision = "0048_crm_module"
branch_labels = None
depends_on = None

NEW_KIND = "change_order_generation"


def upgrade() -> None:
    op.execute("COMMIT")
    exists = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        f"WHERE t.typname = 'ai_job_kind' AND e.enumlabel = '{NEW_KIND}'"
    ).first()
    if not exists:
        op.execute(f"ALTER TYPE ai_job_kind ADD VALUE '{NEW_KIND}'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value. Removing it would mean rebuilding
    # the type and every column using it, which is not worth it for an additive
    # change: an unused label costs nothing.
    pass
