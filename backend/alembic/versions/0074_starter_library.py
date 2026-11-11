"""Starter library: supplier catalogue + construction price index (plan phase 2)

Amir approved 2026-10-15 (Rate and Trade Libraries Plan). Two new read-only
reference tables; no existing table changes. The catalogue is kept out of
material_rates because every active material row goes into the estimate prompt.

Revision ID: 0074_starter_library
Revises: 0073_library_vocabulary
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0074_starter_library"
down_revision = "0073_library_vocabulary"
branch_labels = None
depends_on = None


def _base():
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "catalogue_items", *_base(),
        sa.Column("supplier", sa.Text(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("supplier_group", sa.Text()),
        sa.Column("supplier_category", sa.Text()),
        sa.Column("raw_unit", sa.Text(), nullable=False),
        sa.Column("raw_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.Text()),
        sa.Column("unit_cost", sa.Numeric(12, 4)),
        sa.Column("trade_key", sa.Text()),
        sa.Column("as_of_date", sa.Date()),
        sa.Column("source_ref", sa.Text()),
        sa.UniqueConstraint("supplier", "sku", name="uq_catalogue_items_supplier_sku"),
    )
    op.create_index("ix_catalogue_items_trade_key", "catalogue_items", ["trade_key"])
    op.create_table(
        "cost_index_points", *_base(),
        sa.Column("series", sa.Text(), nullable=False),
        sa.Column("division", sa.Text(), nullable=False),
        sa.Column("ref_period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("series", "division", "ref_period", name="uq_cost_index_points_series_div_period"),
    )


def downgrade() -> None:
    op.drop_table("cost_index_points")
    op.drop_index("ix_catalogue_items_trade_key", table_name="catalogue_items")
    op.drop_table("catalogue_items")
