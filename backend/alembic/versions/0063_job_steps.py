"""Per-step progress on a long AI job.

⚠️ MEASURED JUSTIFICATION, not a guess. Against production 2026-10-02:
`estimate_full` averages 375 seconds and peaks at 456 — over seven and a half
minutes during which the only thing on screen is the word "running". Every other
kind finishes in under a minute (scope_analysis 41s avg, document_summary 16s),
so this exists for one job in particular and the instrumentation is placed
accordingly rather than sprayed everywhere.

A job that shows nothing for seven minutes is indistinguishable from a job that
has hung, which is why people reload the page and start a second one.

Shape: a list, appended to as the job goes.
  [{"name": "architectural", "status": "running"|"done"|"error",
    "started_at": iso, "finished_at": iso|null, "note": str|null}]

A list rather than a count, because "3 of 6" cannot say WHICH three, and the
useful question during a seven-minute wait is "what is it doing now".

Revision ID: 0063_job_steps
Revises: 0062_draft_prov
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0063_job_steps"
down_revision = "0062_draft_prov"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_jobs",
        sa.Column("steps", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("ai_jobs", "steps")
