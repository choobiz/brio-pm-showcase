"""On-site QA checklists — a walkthrough, not an authority inspection.

⚠️ THE DISTINCTION, VERIFIED BEFORE BUILDING. `inspections` carries authority,
inspection_type, lead_time_days and gates, tied to a schedule activity: it
models the City coming to look. It is not, and cannot be stretched into, a
supervisor walking a floor with a list. Checked 2026-10-05: the table exists,
holds 0 rows, and has no column for a result, a deficiency or a photo. No QA
model existed anywhere.

⚠️ THESE ARE BRIO'S OWN CHECKS AND ARE NOT A CODE COMPLIANCE INSTRUMENT.
A passing checklist says our supervisor looked and was satisfied. It does not
say the work meets the BC Building Code, and nothing in this feature may imply
it does — that determination belongs to the authority having jurisdiction and to
the registered professionals on the job. The wording is enforced in the service.

Items live in JSONB on the checklist rather than a second table, matching
daily_site_reports: a checklist is read and written whole, never queried
item-by-item across projects.

  items: [{"id": str, "text": str,
           "result": "pass"|"fail"|"na"|null,     # null = not yet looked at
           "note": str|null, "photos": [doc_uuid]}]

⚠️ null RESULT IS "NOT YET LOOKED AT", NOT "PASS". A checklist where unanswered
reads as satisfactory is worse than no checklist, because it produces a clean
record of an inspection nobody did.

Revision ID: 0064_qa_checklists
Revises: 0063_job_steps
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0064_qa_checklists"
down_revision = "0063_job_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_checklists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # Which template it came from, so a set of checklists can be compared.
        sa.Column("template_key", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        # "Level 2, unit 203" — free text, because how a site is divided is not
        # something this product knows.
        sa.Column("location", sa.Text),
        # open | complete. A checklist is never "passed": individual items pass,
        # and a checklist with failures is still complete.
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("items", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("performed_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_qa_checklists_project_status", "qa_checklists",
                    ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_qa_checklists_project_status", table_name="qa_checklists")
    op.drop_table("qa_checklists")
