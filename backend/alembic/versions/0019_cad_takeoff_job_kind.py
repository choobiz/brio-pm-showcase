"""add cad_takeoff to the ai_job_kind enum

Candidate for the CAD quantity-takeoff skill. Deterministic job kind (no LLM).
"""
from typing import Union

from alembic import op

revision: str = "0019_cad_takeoff_job_kind"
down_revision: Union[str, None] = "0018_page_scale_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # Postgres; use an autocommit block. IF NOT EXISTS keeps it idempotent.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE ai_job_kind ADD VALUE IF NOT EXISTS 'cad_takeoff'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; leaving the value is harmless.
    pass
