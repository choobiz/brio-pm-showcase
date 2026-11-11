"""When a change order went to the client, and when an invoice was sent

The Money tab shows "waiting on client · 12 days" and works out overdue
invoices. Neither could be computed: change orders had no submitted time
(updated_at moves on every edit) and invoices had no sent time, so "overdue"
was only ever a status someone set by hand.

Nullable, no backfill: for rows that were already sent, the time is unknown,
and the page says so rather than guessing from updated_at.

Revision ID: 0070_money_sent_times
Revises: 0069_project_finish_selections
"""
from alembic import op
import sqlalchemy as sa

revision = "0070_money_sent_times"
down_revision = "0069_project_finish_selections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("change_orders", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "sent_at")
    op.drop_column("change_orders", "submitted_at")
