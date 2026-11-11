"""CRM card leads: "send me your details" submissions from a BRIO business card

builtbybrio.com/card/<slug> forwards each submission to POST /public/card-leads.
Amir approved 2026-10-20.

Revision ID: 0079_crm_card_leads
Revises: 0078_submittal_kind
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0079_crm_card_leads"
down_revision = "0078_submittal_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_card_leads",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("company", sa.Text()),
        sa.Column("role", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("card_slug", sa.Text(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("contacted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_crm_card_leads_created_at", "crm_card_leads", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_crm_card_leads_created_at", table_name="crm_card_leads")
    op.drop_table("crm_card_leads")
