"""Add the ai_edit AI job kind.

Editing a scope, estimate or change order by writing an instruction. The job
proposes a change set; nothing is applied by the worker. See
services/edit_plan.py for the shape and why it is a diff rather than a rewrite.

Revision id kept short: alembic_version.version_num is varchar(32), and an
earlier migration in this project failed its bookkeeping UPDATE at 33
characters.

Revision ID: 0053_ai_edit_kind
Revises: 0052_record_revisions
"""
from alembic import op

revision = "0053_ai_edit_kind"
down_revision = "0052_record_revisions"
branch_labels = None
depends_on = None

NEW_KIND = "ai_edit"


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, and
    # Alembic wraps a migration in one. The guard makes it re-runnable: adding
    # a value that already exists is an error, not a no-op.
    op.execute("COMMIT")
    exists = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        f"WHERE t.typname = 'ai_job_kind' AND e.enumlabel = '{NEW_KIND}'"
    ).first()
    if not exists:
        op.execute(f"ALTER TYPE ai_job_kind ADD VALUE '{NEW_KIND}'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value, and an unused label costs nothing.
    pass
