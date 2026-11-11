"""audit_logs must survive the thing it audits

Revision ID: 0046_audit_log_survives
Revises: 0045_qty_override_verify
Create Date: 2026-08-31

`audit_logs.project_id` was `ON DELETE CASCADE`, so deleting a project deleted
its own audit trail. On 2026-08-31 every project in brio-pm was deleted and the
record of that deletion went with them — the table was empty when we came to ask
what had happened, and the cause had to be reconstructed from WAL and autovacuum
timestamps instead.

An audit log that a deletion can erase is not an audit log. `project_id` becomes
nullable with `ON DELETE SET NULL`: the project goes, the record of what happened
to it stays, with the id preserved in `diff` so the orphaned row is still
attributable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0046_audit_log_survives"
down_revision = "0045_qty_override_verify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The constraint is named by the project's naming convention, not Postgres's
    # default — assuming the default name failed the whole migration.
    op.drop_constraint("fk_audit_logs_project_id_projects", "audit_logs",
                       type_="foreignkey")
    op.alter_column("audit_logs", "project_id", existing_type=sa.dialects.postgresql.UUID(),
                    nullable=True)
    op.create_foreign_key("fk_audit_logs_project_id_projects", "audit_logs",
                          "projects", ["project_id"], ["id"], ondelete="SET NULL")
    # Keep the project id in the payload, so a row orphaned by a deletion can
    # still be traced back to what it described.
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_logs_keep_project_id() RETURNS trigger AS $$
        BEGIN
          IF NEW.project_id IS NOT NULL THEN
            NEW.diff = coalesce(NEW.diff, '{}'::jsonb)
                       || jsonb_build_object('_project_id', NEW.project_id::text);
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_keep_project_id ON audit_logs")
    op.execute("""
        CREATE TRIGGER trg_audit_logs_keep_project_id
        BEFORE INSERT ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_keep_project_id();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_keep_project_id ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_keep_project_id()")
    # The constraint is named by the project's naming convention, not Postgres's
    # default — assuming the default name failed the whole migration.
    op.drop_constraint("fk_audit_logs_project_id_projects", "audit_logs",
                       type_="foreignkey")
    op.execute("DELETE FROM audit_logs WHERE project_id IS NULL")
    op.alter_column("audit_logs", "project_id", existing_type=sa.dialects.postgresql.UUID(),
                    nullable=False)
    op.create_foreign_key("fk_audit_logs_project_id_projects", "audit_logs",
                          "projects", ["project_id"], ["id"], ondelete="CASCADE")
