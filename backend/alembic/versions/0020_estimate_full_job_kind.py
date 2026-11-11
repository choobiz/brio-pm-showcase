"""add estimate_full to the ai_job_kind enum

Full multi-discipline estimate (scope-based or CAD-based) — Phase 2 two-button flow.
"""
from typing import Union

from alembic import op

revision: str = "0020_estimate_full_job_kind"
down_revision: Union[str, None] = "0019_cad_takeoff_job_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ai_job_kind ADD VALUE IF NOT EXISTS 'estimate_full'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; leaving the value is harmless.
    pass
