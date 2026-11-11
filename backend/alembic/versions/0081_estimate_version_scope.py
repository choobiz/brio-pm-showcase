"""estimate_versions.scope_id: the scope an estimate was priced from.

A project can carry one scope per job (456 W Sample: Suite 100 and the 4th
floor corridor), and the "scope + selections + lump sum" PDF must put an
estimate's own scope in front of it. The link existed only indirectly: the
generating job's input named the scope, and revisions point at their parent.
Amir, 2026-10-27: store it on each estimate.

Backfill, in this order:
  1. generated versions: the scope their ai_job's input names, when that scope
     still exists on the same project;
  2. revisions: inherit their parent's scope, repeated down the chain.
Versions with neither (typed in or imported) stay NULL. Nothing is guessed.

Revision ID: 0081_estimate_version_scope
Revises: 0080_drawing_analysis_kind
"""
from __future__ import annotations

from alembic import op

revision = "0081_estimate_version_scope"
down_revision = "0080_drawing_analysis_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE estimate_versions
          ADD COLUMN IF NOT EXISTS scope_id uuid REFERENCES scopes(id) ON DELETE SET NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_estimate_versions_scope_id ON estimate_versions (scope_id)")
    op.execute("""
        UPDATE estimate_versions ev
           SET scope_id = s.id
          FROM ai_jobs j, scopes s
         WHERE j.id = ev.ai_job_id
           AND s.id::text = j.input->>'scope_id'
           AND s.project_id = ev.project_id
           AND ev.scope_id IS NULL
    """)
    op.execute("""
        WITH RECURSIVE chain AS (
            SELECT id, scope_id FROM estimate_versions WHERE scope_id IS NOT NULL
            UNION ALL
            SELECT child.id, chain.scope_id
              FROM estimate_versions child
              JOIN chain ON child.parent_version_id = chain.id
             WHERE child.scope_id IS NULL
        )
        UPDATE estimate_versions ev
           SET scope_id = chain.scope_id
          FROM chain
         WHERE ev.id = chain.id AND ev.scope_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_estimate_versions_scope_id")
    op.execute("ALTER TABLE estimate_versions DROP COLUMN IF EXISTS scope_id")
