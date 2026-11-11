"""Full prior-state snapshots for scopes, estimates and change orders.

Today every one of these is edited by wholesale replacement:

    scope.payload = payload.payload
    db.commit()

There is no history. `audit_logs` exists and records that an edit happened,
with a diff, for exactly one table — it held 6 rows when this was written, all
`estimate_versions`. Scope and estimate edits were not recorded at all.

That is survivable while a human is typing into a form and can retype. It stops
being survivable the moment an AI is allowed to rewrite the document from a
sentence of instruction, which is what this table is a prerequisite for: one
bad instruction would destroy a scope with nothing to go back to.

A full prior payload, not a diff. A diff tells you what changed; only the whole
prior state lets you put it back, and putting it back is the entire point.

Revision ID: 0052_record_revisions
Revises: 0051_document_selection
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0052_record_revisions"
down_revision = "0051_document_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "record_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Which row this is a snapshot of. Kept as text + uuid rather than a
        # foreign key per target table, so one table serves all three records
        # and anything added later.
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Monotonic per (table_name, row_id). 1 is the state before the first
        # recorded edit.
        sa.Column("revision", sa.Integer(), nullable=False),
        # The COMPLETE state before the edit that created this row.
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_record_revisions"),
        sa.UniqueConstraint("table_name", "row_id", "revision",
                            name="uq_record_revisions_row_revision"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"],
                                name="fk_record_revisions_project_id_projects",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"],
                                name="fk_record_revisions_actor_id_users",
                                ondelete="SET NULL"),
    )
    # The history panel asks "every revision of this row, newest first".
    op.create_index("ix_record_revisions_row", "record_revisions",
                    ["table_name", "row_id", "revision"])
    op.create_index("ix_record_revisions_project", "record_revisions",
                    ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_record_revisions_project", table_name="record_revisions")
    op.drop_index("ix_record_revisions_row", table_name="record_revisions")
    op.drop_table("record_revisions")
