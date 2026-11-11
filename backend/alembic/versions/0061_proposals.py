"""Proposals — a change somebody suggested, and what was decided about it.

⚠️ THE GAP THIS CLOSES. Today an ai_edit job produces a change set, the plan is
read, and it is either applied or it is not. If it is not, NOTHING RECORDS THAT
IT EXISTED. The acceptance criterion in §11 is explicit: "Reject a proposal; who
proposed, what it would have changed, who rejected and why are all retrievable.
All four present." None of those four survive a decline today.

A rejected proposal is therefore kept, permanently, exactly as an accepted one
is. Deleting it would destroy the only record of a decision that was taken — and
"we considered raising that and chose not to" is the answer a dispute turns on
far more often than "we did it".

WHY A ROW AND NOT JUST AN ai_jobs OUTPUT. A job is one model run. A proposal
outlives it: it can be superseded by a later one, go stale because the record
moved underneath it, or be accepted days after it was produced. It also has an
author who is not always a job — a skill, or eventually a person. The lifecycle
belongs to the proposal, not to the run that happened to produce it.

`decision_reason` is NOT NULL-checked at the service layer for rejections
specifically. A rejection with no reason is an audit entry that answers three of
the four questions and leaves the useful one blank.

Revision ID: 0061_proposals
Revises: 0060_daily_capture
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0061_proposals"
down_revision = "0060_daily_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),

        # What it would change. Not a ForeignKey: the target is one of several
        # tables and a polymorphic FK cannot be expressed. The service checks the
        # table name against an allow-list instead, so a typo cannot address a
        # table nobody intended.
        sa.Column("target_table", sa.Text, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),

        # Where it came from.
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("ai_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ai_jobs.id", ondelete="SET NULL")),
        # SET NULL rather than CASCADE: a departed employee's proposals are still
        # part of the record of what was decided.
        sa.Column("proposed_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),

        # What it would do, in one line a person can read without opening it.
        sa.Column("summary", sa.Text, nullable=False),
        # The verified change set — each entry carries the `before` it expects.
        sa.Column("change_set", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        # What else this would touch. Computed at propose time and recomputed
        # before accepting, because the answer can change underneath it.
        sa.Column("impact", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),

        # pending | accepted | rejected | stale | superseded
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("decided_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text),

        # The snapshot taken when it was applied, so an accepted proposal can be
        # put back. Null for anything not accepted.
        #
        # ⚠️ An INTEGER, not a uuid: services/revisions.snapshot() returns a
        # revision NUMBER, and a revision is identified by
        # (table, row_id, number). A uuid column here would have been null on
        # every accepted proposal while looking like it held a link.
        sa.Column("revision_number", sa.Integer),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # The register and the record's own history both read by target.
    op.create_index("ix_proposals_target", "proposals", ["target_table", "target_id"])
    # "What is waiting for me on this project" — the common read.
    op.create_index("ix_proposals_project_status", "proposals", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_proposals_project_status", table_name="proposals")
    op.drop_index("ix_proposals_target", table_name="proposals")
    op.drop_table("proposals")
