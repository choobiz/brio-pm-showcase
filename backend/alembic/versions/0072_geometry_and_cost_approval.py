"""Confirmed project geometry, forming calculator settings, and cost approval

Amir, 2026-10-13: results must be consistent, and every assistant request
shows an approximate cost that the person approves before it runs.

- project_geometries: the foundation outline, sizes and interior walls a
  person CONFIRMS once; the forming calculator counts from it, so the same
  geometry always gives the same quantities, on the Mac or the server.
- app_settings 'forming_params': the calculator's BRIO numbers, editable.
- prep_requests.approved_usd: the most the server may spend on the request
  (cumulative); ai_spend.stage: which step a call belonged to, for estimates.

Revision ID: 0072_geometry_and_cost_approval
Revises: 0071_prepared_documents
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0072_geometry_and_cost_approval"
down_revision = "0071_prepared_documents"
branch_labels = None
depends_on = None

FORMING_PARAMS = {
    "stake_spacing_ft": 4, "board_length_ft": 16, "spare_pct": 10, "stakes_per_corner": 2,
    "spreader_spacing_ft": 4, "spreader_cut_in": 30, "spreader_stock_ft": 10,
    "cleat_length_in": 24, "cleat_stock_ft": 8,
    "nails": [{"item": "Duplex (double-head) nails", "spec": "3-1/2 in (16d)", "qty": 10, "unit": "lb"},
              {"item": "Duplex (double-head) nails", "spec": "2-1/2 in (8d)", "qty": 5, "unit": "lb"}],
}


def upgrade() -> None:
    op.create_table(
        "project_geometries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.Text, nullable=False, server_default="foundation"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.Text, nullable=False, server_default="proposed"),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("source", sa.Text),
        sa.Column("prep_request_id", UUID(as_uuid=True), sa.ForeignKey("prep_requests.id", ondelete="SET NULL")),
        sa.Column("confirmed_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("prep_requests", sa.Column("approved_usd", sa.Numeric(10, 4), nullable=False, server_default="0"))
    op.add_column("ai_spend", sa.Column("stage", sa.Text))
    op.get_bind().execute(sa.text("INSERT INTO app_settings (key, value, updated_at) VALUES "
                                  "('forming_params', cast(:v as jsonb), now()) ON CONFLICT (key) DO NOTHING"),
                          {"v": json.dumps(FORMING_PARAMS)})


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'forming_params'")
    op.drop_column("ai_spend", "stage")
    op.drop_column("prep_requests", "approved_usd")
    op.drop_table("project_geometries")
