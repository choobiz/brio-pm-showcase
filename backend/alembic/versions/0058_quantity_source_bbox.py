"""Record WHERE on the drawing a quantity was measured

⚠️ THE GAP THIS CLOSES. "Show me where this number came from" could be answered
only down to the DOCUMENT. `source_page` existed but was null on every row;
`source_candidate_key` held "cad:floor_area_sf" — a category, not a locator. So
a reviewer confirming a floor area had no way to check it except by opening the
sheet and eyeballing the outline.

Meanwhile the geometry was being computed and thrown away. A PDF analysis of one
real site plan produces 254 candidates, every one carrying a bbox in PDF units —
and `bboxToScreen()` on the client already converts those to screen pixels with
page rotation handled. The round trip was one column short.

COORDINATE CONVENTION, stated because a bbox without one is a guess:
    [x0, y0, x1, y1] in PDF user-space units, as PyMuPDF reports them, BEFORE
    page rotation is applied. pdf.js rotates the canvas; the client rotates the
    box to match. Storing post-rotation coordinates would bake in a viewer's
    behaviour and break the moment a page is re-rotated.

⚠️ PDF ONLY. A CAD-derived quantity gets no bbox: DXF model-space coordinates
have no relationship to a printed sheet without a known viewport, scale and
origin. That transform is real work and is deliberately not attempted here, so
`source_bbox` stays null for the CAD path and the interface must keep saying it
cannot point at the drawing for those.

Revision ID: 0058_quantity_source_bbox
Revises: 0057_drawing_revisions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0058_quantity_source_bbox"
down_revision = "0057_drawing_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quantity_overrides",
                  sa.Column("source_bbox", postgresql.JSONB(), nullable=True))
    # Which revision of the drawing was read. A quantity measured off a sheet
    # that has since been superseded is not wrong, but it is not current either,
    # and nothing could express that difference before.
    op.add_column("quantity_overrides",
                  sa.Column("source_revision_label", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("quantity_overrides", "source_revision_label")
    op.drop_column("quantity_overrides", "source_bbox")
