"""projects.latitude/longitude — geocoded site coordinates for punch distance-flagging

Lazily populated from projects.address via Nominatim the first time a project's
Hours view is loaded. Used to flag punches recorded far from the job site.

Revision ID: 0030_project_geocode
Revises: 0029_time_entries
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0030_project_geocode"
down_revision = "0029_time_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("projects", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))
    # NULL = not yet attempted; a stored timestamp = we tried (avoids re-hitting
    # the geocoder every load for addresses that can't be resolved).
    op.add_column("projects", sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "geocoded_at")
    op.drop_column("projects", "longitude")
    op.drop_column("projects", "latitude")
