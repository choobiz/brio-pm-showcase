"""drawing_analysis_packs — the reviewed snapshot an estimate is allowed to price

Revision ID: 0047_analysis_packs
Revises: 0046_audit_log_survives
Create Date: 2026-08-31

Phase 2 of the verification gate. Phase 1 gave individual quantities a status;
this gives the REVIEW itself an identity — what was analysed, from which
documents, how accurate the set was judged to be, and who signed it off.

Quantities deliberately stay in `quantity_overrides` rather than being copied in
here. A second copy of a number is a second thing to keep in sync, and the whole
incident this work follows came from two representations of the same fact
disagreeing silently.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0047_analysis_packs"
down_revision = "0046_audit_log_survives"
branch_labels = None
depends_on = None

STATUSES = ("draft", "approved", "superseded")


def upgrade() -> None:
    status = sa.Enum(*STATUSES, name="drawing_analysis_pack_status")
    status.create(op.get_bind(), checkfirst=True)
    # create_type=False: the type is created explicitly above, and letting the
    # column create it too issues a second CREATE TYPE that fails the migration.
    status_col = postgresql.ENUM(*STATUSES, name="drawing_analysis_pack_status",
                                 create_type=False)
    op.create_table(
        "drawing_analysis_packs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", status_col, nullable=False, server_default="draft"),
        # audit + accuracy + per-sheet takeoff summaries, as produced at build time
        sa.Column("payload", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # which documents were read, so a later upload can be detected as newer
        sa.Column("source_document_ids", postgresql.JSONB(), nullable=True),
        sa.Column("approved_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_analysis_packs_project", "drawing_analysis_packs",
                    ["project_id", "version_number"])
    # At most one approved pack per project — "the approved analysis" has to mean
    # one thing when an estimate asks whether it may run.
    op.create_index("uq_analysis_packs_approved_per_project", "drawing_analysis_packs",
                    ["project_id"], unique=True,
                    postgresql_where=sa.text("status = 'approved'"))


def downgrade() -> None:
    op.drop_index("uq_analysis_packs_approved_per_project",
                  table_name="drawing_analysis_packs")
    op.drop_index("ix_analysis_packs_project", table_name="drawing_analysis_packs")
    op.drop_table("drawing_analysis_packs")
    sa.Enum(name="drawing_analysis_pack_status").drop(op.get_bind(), checkfirst=True)
