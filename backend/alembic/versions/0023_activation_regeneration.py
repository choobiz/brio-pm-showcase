"""activation regeneration: schedules.based_on_estimate_id + regeneration_approvals ledger

Phase B — when a different estimate version is activated, its downstream
(scope, schedule, cash flow, SOV, contract, presentation) is regenerated after a
single management approval. The ledger records who approved and the token cost.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0023_activation_regeneration"
down_revision: Union[str, None] = "0022_estimate_anchored_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column(
        "based_on_estimate_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_schedules_based_on_estimate", "schedules", ["based_on_estimate_id"])

    op.create_table(
        "regeneration_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("estimate_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_name", sa.Text(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"),
        # per-artifact plan + enqueued job ids
        sa.Column("artifacts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_regen_approvals_project", "regeneration_approvals", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_regen_approvals_project", table_name="regeneration_approvals")
    op.drop_table("regeneration_approvals")
    op.drop_index("ix_schedules_based_on_estimate", table_name="schedules")
    op.drop_column("schedules", "based_on_estimate_id")
