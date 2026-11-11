"""schedules.client_visible: which versions the client may see.

The portal and the client package read the latest schedule. A re-plan saved
from Schedule › Record what happened must not reach the client until the
office ticks "Tell the client", so versions carry a flag and client readers
take the latest visible one. Every existing version stays visible (the default),
so nothing the client sees changes with this migration.

Revision ID: 0083_schedule_client_visible
Revises: 0082_activity_actuals
"""
from alembic import op
import sqlalchemy as sa

revision = "0083_schedule_client_visible"
down_revision = "0082_activity_actuals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("client_visible", sa.Boolean(), nullable=False,
                                         server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("schedules", "client_visible")
