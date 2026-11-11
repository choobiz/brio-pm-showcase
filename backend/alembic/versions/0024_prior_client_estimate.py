"""prior client estimate: projects.prior_client_estimate

The figure/date/context of an estimate already sent to the client. Persisted on
the project so the presentation's "why this is higher than our earlier estimate"
section survives every (re)generation instead of being a one-off manual input.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0024_prior_client_estimate"
down_revision: Union[str, None] = "0023_activation_regeneration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column(
        "prior_client_estimate", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "prior_client_estimate")
