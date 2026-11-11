"""Give a change order somewhere to keep its pricing breakdown.

A generated change order arrived with line items, assumptions and exclusions,
and the table had nowhere to put them: only title, description, cost_impact and
schedule_impact_days. The breakdown was landing on ai_jobs.output, which the
change-orders page never reads and which is not tied to the change order's own
lifecycle. So the figure was visible and the working behind it was not, which is
the opposite of what a change order is for: a number you can defend line by line
to a client who is being asked to pay it.

Revision ID: 0050_change_order_detail
Revises: 0049_change_order_kind
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0050_change_order_detail"
down_revision = "0049_change_order_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "change_orders",
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("change_orders", "detail")
