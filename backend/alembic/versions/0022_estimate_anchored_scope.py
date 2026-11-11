"""estimate-anchored scope: scopes.based_on_estimate_id + scope/schedule-from-estimate job kinds

Makes the scope hinge on an estimate version. After v1 (scope-from-documents),
each new estimate version derives its own scope (and schedule) so all downstream
documents stay consistent with the estimate that is the source of truth.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0022_estimate_anchored_scope"
down_revision: Union[str, None] = "0021_presentation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ai_job_kind ADD VALUE IF NOT EXISTS 'scope_from_estimate'")
        op.execute("ALTER TYPE ai_job_kind ADD VALUE IF NOT EXISTS 'schedule_from_estimate'")

    op.add_column("scopes", sa.Column(
        "based_on_estimate_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_scopes_based_on_estimate", "scopes", ["based_on_estimate_id"])


def downgrade() -> None:
    op.drop_index("ix_scopes_based_on_estimate", table_name="scopes")
    op.drop_column("scopes", "based_on_estimate_id")
    # enum values are left in place (Postgres has no DROP VALUE)
