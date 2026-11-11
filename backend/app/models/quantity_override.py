from decimal import Decimal
from uuid import UUID

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


#: Verification state. `status`, not `source`, decides whether a quantity may be
#: priced — a CAD extraction and a human confirmation can share a source.
QUANTITY_OVERRIDE_STATUSES = ("candidate", "confirmed", "corrected", "excluded")

#: The only states an estimate may consume.
PRICEABLE_STATUSES = ("confirmed", "corrected")


class QuantityOverride(UUIDPKMixin, TimestampMixin, Base):
    """A quantity for a project/discipline, and how far it has been verified.

    `status` is the gate. `candidate` is what a machine produced and nobody has
    looked at; `confirmed` is a human agreeing with it; `corrected` is a human
    replacing it (with `machine_value` keeping what the machine had said, so the
    gap between the two stays visible); `excluded` is a human saying it does not
    apply. Only confirmed and corrected reach an estimate.

    Three source kinds:
      - candidate_confirmed: the user clicked ✓ on an AI-detected linear
        candidate in the review UI. ``source_candidate_key`` is the stable
        identifier ``f"{page}:{classification}:{width_bucket}:{ortho}"``.
      - manual_measure: the user drew a line on the PDF; value is the
        computed length using the page scale.
      - manual_entry: the user typed a value into a field directly.
    """

    __tablename__ = "quantity_overrides"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    discipline: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"),
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_candidate_key: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(*QUANTITY_OVERRIDE_STATUSES, name="quantity_override_status"),
        nullable=False, default="candidate", server_default="candidate", index=True,
    )
    #: What the extractor said, kept beside a human correction.
    machine_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    confirmed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Where on the sheet this was measured: [x0, y0, x1, y1] in PDF user-space
    #: units as PyMuPDF reports them, BEFORE page rotation. pdf.js rotates the
    #: canvas and the client rotates the box to match; storing post-rotation
    #: coordinates would bake a viewer's behaviour into the record.
    #:
    #: ⚠️ PDF ONLY. Null for anything read from CAD — DXF model-space has no
    #: relationship to a printed sheet without a known viewport, scale and
    #: origin, and that transform is not attempted. The interface must keep
    #: saying it cannot point at the drawing for those.
    source_bbox: Mapped[dict | None] = mapped_column(JSONB)
    #: Which revision of the drawing was read. A quantity measured off a sheet
    #: that has since been superseded is not wrong, but it is not current.
    source_revision_label: Mapped[str | None] = mapped_column(Text)
