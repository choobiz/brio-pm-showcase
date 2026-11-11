"""Recent permit projects per prospect, for outreach that names real addresses.

The CRM's draft scripts were built from permit AGGREGATES — "228 permits as
applicant, $3M of project value in Vancouver". That is true and it is also what
every other cold approach sounds like. The prospecting pipeline already carried
the individual projects behind those counts; the import simply never brought
them across, so the one detail that proves you looked at their actual work was
sitting in a JSON file on the laptop.

The Tier B next-action written at import time reads "Email referencing two or
three of their actual recent projects". The intent predates this column by three
weeks; only the data was missing.

Shape: a JSON array of {address, value, work, issued, permit}, newest first, at
most three. JSONB rather than a child table because it is a denormalised
snapshot of an external pipeline, rewritten wholesale on each refresh and never
queried across firms.

Revision ID: 0054_client_recent_proj
Revises: 0053_ai_edit_kind
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0054_client_recent_proj"
down_revision = "0053_ai_edit_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("recent_projects", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clients", "recent_projects")
