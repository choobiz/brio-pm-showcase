"""activity_actuals + schedules.is_baseline.

activity_actuals: what actually happened to a schedule activity, keyed by
(project, activity_code) so it survives re-versioning. Holds typed-in facts
only; facts from daily site reports are derived on read.

schedules.is_baseline: the version "compare with plan" measures against. One
per project. 1234 Example: v2 (first schedule after the DNV permit), chosen by
Amir 2026-10-29.

Revision ID: 0082_activity_actuals
Revises: 0081_estimate_version_scope
"""
from __future__ import annotations

from alembic import op

revision = "0082_activity_actuals"
down_revision = "0081_estimate_version_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS activity_actuals (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            activity_code text NOT NULL,
            status text CHECK (status IN ('done', 'started', 'not_started')),
            actual_start date,
            actual_finish date,
            percent_complete integer CHECK (percent_complete BETWEEN 0 AND 100),
            reason text,
            recorded_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_activity_actual_project_code UNIQUE (project_id, activity_code)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_activity_actuals_project_id ON activity_actuals (project_id)")

    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS is_baseline boolean NOT NULL DEFAULT false")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_one_baseline
                  ON schedules (project_id) WHERE is_baseline""")
    op.execute("""
        UPDATE schedules s SET is_baseline = true
          FROM projects p
         WHERE p.id = s.project_id AND p.slug = '1234-example-way-nv' AND s.version_number = 2
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_schedules_one_baseline")
    op.execute("ALTER TABLE schedules DROP COLUMN IF EXISTS is_baseline")
    op.execute("DROP TABLE IF EXISTS activity_actuals")
