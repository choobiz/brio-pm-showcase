"""Cost entry: bills BRIO receives, entered on Money › Costs, and a budget per
division to compare them against.

project_costs has existed since the Drive migration but never held a row. A
bill now also carries its GST (amount stays BEFORE GST), the supplier's
invoice number (a second entry of the same bill is refused), who entered it,
and `category` holds the project's division label ("04 Rough Carpentry &
Framing").

project_cost_budgets: the budget per division. Rows, not one per division, so
the contract's share and each approved change order stay separate lines.

Revision ID: 0084_cost_entry
Revises: 0083_schedule_client_visible
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0084_cost_entry"
down_revision = "0083_schedule_client_visible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_costs", sa.Column("gst", sa.Numeric(12, 2)))
    op.add_column("project_costs", sa.Column("invoice_number", sa.Text()))
    op.add_column("project_costs", sa.Column(
        "created_by_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.create_table(
        "project_cost_budgets",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.func.gen_random_uuid()),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("division", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("project_cost_budgets")
    op.drop_column("project_costs", "created_by_id")
    op.drop_column("project_costs", "invoice_number")
    op.drop_column("project_costs", "gst")
