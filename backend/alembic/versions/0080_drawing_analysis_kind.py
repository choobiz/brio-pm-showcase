"""Add the drawing_analysis job kind.

Parsing a drawing PDF takes 15-20 s and ~600 MB. Done inside a web request it
got the API worker killed by uvicorn's supervisor (2026-10-26, 456 W Sample
corridor set), so it now runs in the background worker.

Revision ID: 0080_drawing_analysis_kind
Revises: 0079_crm_card_leads
"""
from alembic import op

revision = "0080_drawing_analysis_kind"
down_revision = "0079_crm_card_leads"
branch_labels = None
depends_on = None

NEW_KIND = "drawing_analysis"


def upgrade() -> None:
    op.execute("COMMIT")
    exists = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        f"WHERE t.typname = 'ai_job_kind' AND e.enumlabel = '{NEW_KIND}'"
    ).first()
    if not exists:
        op.execute(f"ALTER TYPE ai_job_kind ADD VALUE '{NEW_KIND}'")


def downgrade() -> None:
    pass
