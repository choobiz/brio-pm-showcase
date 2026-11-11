from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class PageScaleOverride(UUIDPKMixin, TimestampMixin, Base):
    """User-set drawing scale for a single (document, page).

    Auto-detection (``services.drawing.scale_detector``) fails on drawings
    whose title-block scale is vectorized/outlined text, or whose notation
    the regex doesn't cover (metric / decimal-foot sets). This table lets a
    human pin the scale so downstream measurement (linear candidates +
    manual line-drops) can compute real feet. Authoritative over the
    auto-detected ``PageScale`` when present.

    Two source kinds:
      - manual_select: the user picked a named scale (e.g. ``1:50`` or
        ``1/4" = 1'-0"``) from the dropdown; ``pdf_units_per_real_foot`` is
        derived server-side from the label.
      - calibrated: the user clicked two points on a known dimension and
        typed its real length; ``pdf_units_per_real_foot`` is the measured
        pixel distance divided by that length.
    """

    __tablename__ = "page_scale_overrides"
    __table_args__ = (
        UniqueConstraint("document_id", "page", name="uq_page_scale_doc_page"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_units_per_real_foot: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), nullable=False,
    )
    scale_label: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
