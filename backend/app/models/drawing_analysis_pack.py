from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

PACK_STATUSES = ("draft", "approved", "superseded")


class DrawingAnalysisPack(UUIDPKMixin, TimestampMixin, Base):
    """One reviewed reading of a project's drawing set.

    Holds what was analysed, from which documents, and how accurate the set was
    judged to be — but NOT the quantities. Those stay in `quantity_overrides`
    with their own review state. Two copies of the same number is two things to
    keep in sync, and this whole line of work exists because two representations
    of one fact disagreed quietly.
    """

    __tablename__ = "drawing_analysis_packs"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*PACK_STATUSES, name="drawing_analysis_pack_status"),
        nullable=False, default="draft", server_default="draft",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                          server_default="{}")
    source_document_ids: Mapped[list | None] = mapped_column(JSONB)
    approved_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
