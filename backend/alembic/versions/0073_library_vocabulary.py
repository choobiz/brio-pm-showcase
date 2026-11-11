"""One vocabulary for the rate and trade libraries (plan phase 1)

Amir, 2026-10-15. Adds the shared trade key to materials, labour rates and
assemblies, and a measure unit + coverage rate to materials so a purchase unit
(sheet, box, gal) converts to what the takeoff measures (sf, lf, ea).
Additive and nullable: existing rows are filed by a reviewed script, not here.

Revision ID: 0073_library_vocabulary
Revises: 0072_geometry_and_cost_approval
"""
import sqlalchemy as sa
from alembic import op

revision = "0073_library_vocabulary"
down_revision = "0072_geometry_and_cost_approval"
branch_labels = None
depends_on = None

_TABLES = ("material_rates", "labour_rates", "rate_assemblies")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("trade_key", sa.Text(), nullable=True))
        op.create_index(f"ix_{t}_trade_key", t, ["trade_key"])
    op.add_column("material_rates", sa.Column("measure_unit", sa.Text(), nullable=True))
    op.add_column("material_rates", sa.Column("coverage", sa.Numeric(12, 4), nullable=True))
    op.create_check_constraint("ck_material_rates_coverage_positive", "material_rates",
                               "coverage IS NULL OR coverage > 0")


def downgrade() -> None:
    op.drop_constraint("ck_material_rates_coverage_positive", "material_rates", type_="check")
    op.drop_column("material_rates", "coverage")
    op.drop_column("material_rates", "measure_unit")
    for t in _TABLES:
        op.drop_index(f"ix_{t}_trade_key", table_name=t)
        op.drop_column(t, "trade_key")
