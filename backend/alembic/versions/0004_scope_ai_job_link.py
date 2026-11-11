"""Link Scope rows back to the AIJob that produced them.

Track C's review UI surfaces provenance — the notes + images Rio analyzed to
produce the scope. The AIJob already points forward (`output.scope_id`); this
adds the reverse FK so the scope page can load its source job directly.

Nullable + ON DELETE SET NULL: scopes imported from brio-admin (or created by
hand) have no originating job, and a purged job must not cascade-delete the
scope a user has since edited.

Revision ID: 0004_scope_ai_job_link
Revises: 0003_nextauth_adapter
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_scope_ai_job_link"
down_revision: Union[str, None] = "0003_nextauth_adapter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scopes",
        sa.Column(
            "ai_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scopes", "ai_job_id")
